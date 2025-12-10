"""Audio recording for hark."""

from __future__ import annotations

import contextlib
import os
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict, cast

import numpy as np
import sounddevice as sd
import soundfile as sf

from hark.audio_sources import (
    AudioSourceInfo,
    InputSource,
    get_devices_for_source,
    validate_source_availability,
)
from hark.constants import DEFAULT_BUFFER_SIZE, DEFAULT_INPUT_SOURCE, DEFAULT_TEMP_DIR
from hark.exceptions import AudioDeviceBusyError, NoLoopbackDeviceError, NoMicrophoneError

__all__ = [
    "AudioDeviceInfo",
    "AudioRecorder",
]


class AudioDeviceInfo(TypedDict):
    """Information about an audio input device."""

    index: int
    name: str
    channels: int
    sample_rate: float


class AudioRecorder:
    """
    Records audio from microphone and/or system audio with real-time level monitoring.

    Streams audio to a temporary WAV file to handle long recordings
    without running out of memory. Supports three input modes:
    - mic: Microphone only (default)
    - speaker: System audio/loopback only
    - both: Simultaneous mic + speaker capture to stereo (L=mic, R=speaker)
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        max_duration: int = 600,
        level_callback: Callable[[float], None] | None = None,
        temp_dir: Path = DEFAULT_TEMP_DIR,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
        input_source: str = DEFAULT_INPUT_SOURCE,
    ) -> None:
        """
        Initialize the audio recorder.

        Args:
            sample_rate: Audio sample rate in Hz.
            channels: Number of audio channels (1 for mono, 2 for stereo).
            max_duration: Maximum recording duration in seconds.
            level_callback: Callback function that receives RMS level (0.0-1.0).
            temp_dir: Directory for temporary audio files.
            buffer_size: Audio buffer size for sounddevice.
            input_source: Input source mode ("mic", "speaker", or "both").
        """
        self._sample_rate = sample_rate
        self._channels = channels
        self._max_duration = max_duration
        self._level_callback = level_callback
        self._temp_dir = temp_dir
        self._buffer_size = buffer_size
        self._input_source = InputSource(input_source)

        self._is_recording = False
        self._start_time: float | None = None
        self._stream: sd.InputStream | None = None
        self._temp_file: Path | None = None
        self._sound_file: sf.SoundFile | None = None
        self._lock = threading.Lock()
        self._frames_written = 0

        # For dual-stream recording (both mode)
        self._mic_stream: sd.InputStream | None = None
        self._speaker_stream: sd.InputStream | None = None
        self._mic_device: AudioSourceInfo | None = None
        self._speaker_device: AudioSourceInfo | None = None
        self._mic_buffer: list[np.ndarray] = []
        self._speaker_buffer: list[np.ndarray] = []
        self._interleave_thread: threading.Thread | None = None
        self._stop_interleave = threading.Event()

    @property
    def is_recording(self) -> bool:
        """Check if recording is in progress."""
        return self._is_recording

    def start(self) -> None:
        """
        Start recording audio from configured source(s).

        Raises:
            NoMicrophoneError: If no microphone is available (for mic/both modes).
            NoLoopbackDeviceError: If no loopback device is available (for speaker/both modes).
            AudioDeviceBusyError: If an audio device is busy.
        """
        if self._is_recording:
            return

        # Validate source availability
        errors = validate_source_availability(self._input_source)
        if errors:
            error_msg = errors[0]
            if "loopback" in error_msg.lower():
                raise NoLoopbackDeviceError()
            raise NoMicrophoneError(error_msg)

        # Get devices for the configured source
        self._mic_device, self._speaker_device = get_devices_for_source(self._input_source)

        # Ensure temp directory exists
        self._temp_dir.mkdir(parents=True, exist_ok=True)

        # Create temporary file
        fd, temp_path = tempfile.mkstemp(suffix=".wav", dir=self._temp_dir)
        self._temp_file = Path(temp_path)

        # Open sound file for writing
        try:
            self._sound_file = sf.SoundFile(
                self._temp_file,
                mode="w",
                samplerate=self._sample_rate,
                channels=self._channels,
                format="WAV",
                subtype="FLOAT",
            )
        except Exception as e:
            self._temp_file.unlink(missing_ok=True)
            raise AudioDeviceBusyError(f"Failed to create audio file: {e}") from e

        # Start appropriate stream(s) based on input source
        try:
            if self._input_source == InputSource.BOTH:
                self._start_dual_streams()
            else:
                self._start_single_stream()
        except sd.PortAudioError as e:
            self._cleanup()
            if "No Default Input Device" in str(e) or "Invalid device" in str(e):
                if self._input_source == InputSource.SPEAKER:
                    raise NoLoopbackDeviceError() from e
                raise NoMicrophoneError("No microphone detected") from e
            raise AudioDeviceBusyError(f"Audio device error: {e}") from e
        except Exception as e:
            self._cleanup()
            raise AudioDeviceBusyError(f"Failed to start audio stream: {e}") from e

        self._is_recording = True
        self._start_time = time.time()
        self._frames_written = 0

    def _start_single_stream(self) -> None:
        """Start a single input stream for mic or speaker mode."""
        # Determine which device to use
        if self._input_source == InputSource.MIC:
            device = self._mic_device.device_index if self._mic_device else None
        else:  # SPEAKER
            # For PulseAudio loopback, set env var and use 'pulse' device
            if self._speaker_device and self._speaker_device.pulse_source_name:
                os.environ["PULSE_SOURCE"] = self._speaker_device.pulse_source_name
                device = "pulse"
            else:
                device = self._speaker_device.device_index if self._speaker_device else None

        self._stream = sd.InputStream(
            device=device,
            callback=self._audio_callback,
            channels=self._channels,
            samplerate=self._sample_rate,
            blocksize=self._buffer_size,
            dtype=np.float32,
            latency="low",
        )
        self._stream.start()

    def _start_dual_streams(self) -> None:
        """Start both mic and speaker streams for combined capture."""
        # Clear buffers
        self._mic_buffer = []
        self._speaker_buffer = []
        self._stop_interleave.clear()

        # Start mic stream (mono, will be left channel)
        if self._mic_device:
            self._mic_stream = sd.InputStream(
                device=self._mic_device.device_index,
                callback=self._mic_callback,
                channels=1,
                samplerate=self._sample_rate,
                blocksize=self._buffer_size,
                dtype=np.float32,
                latency="low",
            )

        # Start speaker stream (mono, will be right channel)
        if self._speaker_device:
            # For PulseAudio loopback, set env var and use 'pulse' device
            if self._speaker_device.pulse_source_name:
                os.environ["PULSE_SOURCE"] = self._speaker_device.pulse_source_name
                speaker_device: int | str | None = "pulse"
            else:
                speaker_device = self._speaker_device.device_index

            self._speaker_stream = sd.InputStream(
                device=speaker_device,
                callback=self._speaker_callback,
                channels=1,
                samplerate=self._sample_rate,
                blocksize=self._buffer_size,
                dtype=np.float32,
                latency="low",
            )

        # Start both streams
        if self._mic_stream:
            self._mic_stream.start()
        if self._speaker_stream:
            self._speaker_stream.start()

        # Start interleaving thread
        self._interleave_thread = threading.Thread(
            target=self._interleave_buffers,
            daemon=True,
        )
        self._interleave_thread.start()

    def _mic_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: dict,
        status: sd.CallbackFlags,
    ) -> None:
        """Callback for microphone stream in dual-stream mode."""
        if not self._is_recording:
            return

        # Check max duration
        if self._start_time and (time.time() - self._start_time) >= self._max_duration:
            self._is_recording = False
            return

        # Calculate RMS level for UI feedback (use mic for level in both mode)
        if self._level_callback:
            rms = float(np.sqrt(np.mean(indata**2)))
            self._level_callback(rms)

        # Add to buffer
        with self._lock:
            self._mic_buffer.append(indata.copy())

    def _speaker_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: dict,
        status: sd.CallbackFlags,
    ) -> None:
        """Callback for speaker/loopback stream in dual-stream mode."""
        if not self._is_recording:
            return

        # Add to buffer
        with self._lock:
            self._speaker_buffer.append(indata.copy())

    def _interleave_buffers(self) -> None:
        """Thread that interleaves mic and speaker buffers into stereo."""
        while not self._stop_interleave.is_set():
            with self._lock:
                # Process when we have data from both sources
                min_chunks = min(len(self._mic_buffer), len(self._speaker_buffer))

                for _ in range(min_chunks):
                    mic_chunk = self._mic_buffer.pop(0)
                    speaker_chunk = self._speaker_buffer.pop(0)

                    # Ensure same length (take minimum)
                    min_len = min(len(mic_chunk), len(speaker_chunk))
                    mic_chunk = mic_chunk[:min_len]
                    speaker_chunk = speaker_chunk[:min_len]

                    # Interleave: L=mic, R=speaker
                    stereo = np.column_stack(
                        [
                            mic_chunk.flatten(),
                            speaker_chunk.flatten(),
                        ]
                    )

                    # Write to file
                    if self._sound_file is not None and not self._sound_file.closed:
                        try:
                            self._sound_file.write(stereo)
                            self._frames_written += len(stereo)
                        except (sf.SoundFileError, OSError):
                            pass

            # Prevent busy-waiting
            time.sleep(0.01)

    def stop(self) -> Path:
        """
        Stop recording and return the path to the recorded audio file.

        Returns:
            Path to the temporary WAV file containing the recording.
        """
        if not self._is_recording:
            if self._temp_file:
                return self._temp_file
            raise RuntimeError("Recording was never started")

        self._is_recording = False

        # Stop interleave thread if running
        if self._interleave_thread is not None:
            self._stop_interleave.set()
            self._interleave_thread.join(timeout=1.0)
            self._interleave_thread = None

        # Stop and close single stream
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except (sd.PortAudioError, OSError):
                pass
            self._stream = None

        # Stop and close dual streams
        if self._mic_stream is not None:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except (sd.PortAudioError, OSError):
                pass
            self._mic_stream = None

        if self._speaker_stream is not None:
            try:
                self._speaker_stream.stop()
                self._speaker_stream.close()
            except (sd.PortAudioError, OSError):
                pass
            self._speaker_stream = None

        # Flush any remaining buffers for dual-stream mode
        if self._input_source == InputSource.BOTH:
            self._flush_remaining_buffers()

        # Close sound file
        if self._sound_file is not None:
            with contextlib.suppress(OSError):
                self._sound_file.close()
            self._sound_file = None

        if self._temp_file is None:
            raise RuntimeError("No temp file created")

        return self._temp_file

    def _flush_remaining_buffers(self) -> None:
        """Flush any remaining data in the dual-stream buffers."""
        with self._lock:
            min_chunks = min(len(self._mic_buffer), len(self._speaker_buffer))

            for _ in range(min_chunks):
                mic_chunk = self._mic_buffer.pop(0)
                speaker_chunk = self._speaker_buffer.pop(0)

                min_len = min(len(mic_chunk), len(speaker_chunk))
                mic_chunk = mic_chunk[:min_len]
                speaker_chunk = speaker_chunk[:min_len]

                stereo = np.column_stack(
                    [
                        mic_chunk.flatten(),
                        speaker_chunk.flatten(),
                    ]
                )

                if self._sound_file is not None and not self._sound_file.closed:
                    try:
                        self._sound_file.write(stereo)
                        self._frames_written += len(stereo)
                    except (sf.SoundFileError, OSError):
                        pass

            # Clear any remaining unmatched buffers
            self._mic_buffer.clear()
            self._speaker_buffer.clear()

    def get_duration(self) -> float:
        """
        Get the current recording duration in seconds.

        Returns:
            Recording duration in seconds.
        """
        if self._start_time is None:
            return 0.0
        if self._is_recording:
            return time.time() - self._start_time
        # If stopped, calculate from frames written
        return self._frames_written / self._sample_rate

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: dict,
        status: sd.CallbackFlags,
    ) -> None:
        """
        Callback for sounddevice InputStream.

        Args:
            indata: Input audio data.
            frames: Number of frames.
            time_info: Time information.
            status: Status flags.
        """
        if status:
            # Log status issues (could add logging here)
            pass

        if not self._is_recording:
            return

        # Check max duration
        if self._start_time and (time.time() - self._start_time) >= self._max_duration:
            self._is_recording = False
            return

        # Calculate RMS level for UI feedback
        if self._level_callback:
            rms = float(np.sqrt(np.mean(indata**2)))
            self._level_callback(rms)

        # Write to file (thread-safe)
        with self._lock:
            if self._sound_file is not None and not self._sound_file.closed:
                try:
                    self._sound_file.write(indata)
                    self._frames_written += len(indata)
                except (sf.SoundFileError, OSError):
                    pass

    def _cleanup(self) -> None:
        """Clean up resources."""
        # Stop interleave thread
        if self._interleave_thread is not None:
            self._stop_interleave.set()
            self._interleave_thread.join(timeout=1.0)
            self._interleave_thread = None

        # Clean up single stream
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        # Clean up dual streams
        if self._mic_stream is not None:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except Exception:
                pass
            self._mic_stream = None

        if self._speaker_stream is not None:
            try:
                self._speaker_stream.stop()
                self._speaker_stream.close()
            except Exception:
                pass
            self._speaker_stream = None

        # Clear buffers
        self._mic_buffer.clear()
        self._speaker_buffer.clear()

        if self._sound_file is not None:
            with contextlib.suppress(Exception):
                self._sound_file.close()
            self._sound_file = None

        if self._temp_file is not None:
            with contextlib.suppress(Exception):
                self._temp_file.unlink(missing_ok=True)
            self._temp_file = None

    @staticmethod
    def list_devices() -> list[AudioDeviceInfo]:
        """
        List available audio input devices.

        Returns:
            List of AudioDeviceInfo dictionaries.
        """
        devices: list[AudioDeviceInfo] = []
        for i, device in enumerate(sd.query_devices()):
            if device["max_input_channels"] > 0:
                devices.append(
                    AudioDeviceInfo(
                        index=i,
                        name=device["name"],
                        channels=device["max_input_channels"],
                        sample_rate=device["default_samplerate"],
                    )
                )
        return devices

    @staticmethod
    def get_default_device() -> AudioDeviceInfo | None:
        """
        Get the default audio input device.

        Returns:
            AudioDeviceInfo, or None if no default device.
        """
        try:
            device_id = sd.default.device[0]
            if device_id is None:
                return None
            device = cast(dict[str, Any], sd.query_devices(device_id))
            return AudioDeviceInfo(
                index=int(device_id),
                name=str(device["name"]),
                channels=int(device["max_input_channels"]),
                sample_rate=float(device["default_samplerate"]),
            )
        except Exception:
            return None
