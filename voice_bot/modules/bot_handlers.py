from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    User,
    Message
)
from telegram.error import TelegramError
from telegram.ext import CallbackContext, Application, ContextTypes
import time
import os
from modules.bot_utils import (
    validate_text,
    convert_to_voice,
    clear_dir,
    user_restricted,
    log_cmd,
    get_user_voice_dir,
    answer_query,
    logger,
    MAX_CHARS_NUM,
    RESULTS_PATH
)
from modules.tortoise_api import tts_audio_from_text
from modules.bot_db import db_handle
from modules.bot_settings_menu import get_user_settings, UserSettings
import asyncio
from concurrent.futures import Future
from threading import Thread


QUERY_PATTERN_RETRY = "c_re"
SOURCE_WEB_LINK = "https://github.com/Helther/voice-pick-tbot"


class TTSWorkThread(Thread):
    """
    Thread class with active event loop to process incoming synthesis requests sequentially
    on separate thread
    """
    def __init__(self):
        Thread.__init__(self, name="tts_worker", daemon=True)  # Doesn't matter if stops unexpectedly
        self.loop = asyncio.new_event_loop()

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()


tts_work_thread = TTSWorkThread()


@user_restricted
async def start_cmd(update: Update, context: CallbackContext) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    db_handle.init_user(user.id)
    await update.message.reply_html(f"Hi, {user.mention_html()}! Call /help to get info about bot usage")


@user_restricted
async def gen_audio_cmd(update: Update, context: CallbackContext) -> None:
    """Send voice audio file generated by inference"""
    reply_id = update.message.message_id
    if not context.args:
        await update.message.reply_text("Error: invalid arguments provided, provide text next to the command", reply_to_message_id=reply_id)
        return

    text = ' '.join(context.args)
    if not validate_text(text):
        await update.message.reply_text("Error: Invalid text detected",
                                        reply_to_message_id=reply_id)
        return

    context.application.create_task(start_gen_task(update, context, text), update=update)


@user_restricted
async def retry_button(update: Update, context: CallbackContext) -> None:
    """launches tts task on a already completed one from the message keyboard"""
    query = update.callback_query
    context.application.create_task(answer_query(query), update=update)

    # TODO get actual message text instead of caption
    context.application.create_task(start_gen_task(update, context, query.message.caption), update=update)


async def help_cmd(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    log_cmd(user, "help_cmd")
    help_msg = ("Bot usage: select from the menu or type commands to interact with the bot. List of commands:\n"
                "<u>/gen</u> - provide text with this command and evenrually receive a voice reply with your query,"
                ",it may takes some time, depending on text length (from couple of seconds for a short sentence to "
                "couple of minutes for essays)\n"
                "<u>/add_voice</u> - to start a guided process of adding user voice for cloning, by providing the name and audio samples "
                "via files or voice recording\n"
                "<u>/settings</u> - change user specific settings for voice synthesis\n"
                f"Take a look at source code for additional info at <a href='{SOURCE_WEB_LINK}'>GitHub</a>")
    await update.message.reply_html(help_msg)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    if update and update.effective_message and update.effective_user:
        await update.effective_message.reply_html(f"Sorry {update.effective_user.mention_html()}, there has been a Server Internal Error", reply_to_message_id=update.effective_message.message_id)


""" ------------------------------TTS related callbacks------------------------------ """


async def start_gen_task(update: Update, context: CallbackContext, text: str) -> None:
    user = update.effective_user
    filename_result = os.path.abspath(os.path.join(RESULTS_PATH, '{}_{}.wav'.format(user.id, int(time.time()))))
    settings = get_user_settings(user.id)
    future = asyncio.run_coroutine_threadsafe(run_gen_audio(update, context.application, filename_result, settings, text, get_user_voice_dir(user.id)), tts_work_thread.loop)
    future.add_done_callback(eval_gen_task)


async def run_gen_audio(update: Update, app: Application, filename_result: str, settings: UserSettings, text: str, user_voices_dir: str) -> None:
    tts_audio_from_text(filename_result, text, settings.voice, user_voices_dir, settings.emotion, settings.samples_num)
    return update, app, filename_result, text, settings.samples_num


def eval_gen_task(future: Future) -> None:
    exc = None
    try:
        update, app, filename_result, text, samples_num = future.result()
    except Exception as e:
        exc = e
    app.create_task(post_eval_gen_task(update.effective_user, filename_result, text, samples_num, update.effective_message, exc), update=update)


async def post_eval_gen_task(user: User, filename_result: str, text: str, samples_num: int, message: Message, exc) -> None:
    try:
        if exc:  # propagate error from Future
            raise exc
        for sample_ind in range(samples_num):
            sample_file = filename_result.replace(".wav", f"_{sample_ind}.wav")
            voice_file = convert_to_voice(sample_file)
            with open(voice_file, 'rb') as audio:
                keyboard = [[InlineKeyboardButton("Regenerate", callback_data=QUERY_PATTERN_RETRY)]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                if len(text) > MAX_CHARS_NUM:  # elide to prevent hitting max caption size
                    text = f"{text[:MAX_CHARS_NUM]}..."
                await message.reply_voice(voice=audio, caption=text, reply_to_message_id=message.message_id, reply_markup=reply_markup)

            logger.info(f"Audio generation DONE: called by {user.full_name}, for sample №{sample_ind}, with query: {text}")
    except Exception as e:
        clear_dir(RESULTS_PATH)
        raise TelegramError("Audio generation Error") from e
    else:
        clear_dir(RESULTS_PATH)
