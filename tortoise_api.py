import tortoise.api
from tortoise.utils import audio
import torchaudio
from torch.cuda import empty_cache
from torch import cat
from typing import List, Tuple
from tortoise.utils.text import split_and_recombine_text
from bot_utils import MODELS_PATH, VOICES_PATH, config


# init tts models
tts = tortoise.api.TextToSpeech(models_dir=MODELS_PATH, high_vram=config.high_vram, autoregressive_batch_size=config.batch_size, device=config.device)


def run_tts_on_text(filename: str, text: str, voice: str, candidates: int) -> List[Tuple]:
    """save result into file with filename, returns audio data and filename pairs"""
    result = []
    voice_samples, conditioning_latents = audio.load_voice(voice, [VOICES_PATH])
    pcm_audio = tts.tts_with_preset(text, voice_samples=voice_samples, conditioning_latents=conditioning_latents, preset="ultra_fast", k=candidates)
    pcm_audio = pcm_audio if candidates > 1 else [pcm_audio]
    for candidate_ind, sample in enumerate(pcm_audio):
        sample_file = f"{filename}_{candidate_ind}.wav"
        sample = sample.squeeze(0).cpu()
        torchaudio.save(sample_file, sample, 24000)
        result.append((sample, sample_file))

    return result


# TODO add multiple samples support
def tts_audio_from_text(filename_result: str, text: str, voice: str) -> None:
    audio_clips = []
    clipname_result = filename_result.replace(".wav", "")
    clips = split_and_recombine_text(text)
    try:
        for clip_ind, clip in enumerate(clips):
            clip_name = f"{clipname_result}_{clip_ind}"
            samples_data = run_tts_on_text(clip_name, clip, voice, 1)
            audio_clips.append(samples_data[0][0])  # audio data of the first candidate

        audio_combined = cat(audio_clips, dim=-1)
        torchaudio.save(filename_result, audio_combined, 24000)

    finally:
        if config.keep_cache:
            empty_cache()
