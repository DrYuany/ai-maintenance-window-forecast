export function setPlaybackSpeed(audio: HTMLAudioElement, speed: number) { audio.playbackRate = speed; audio.preservesPitch = true; }
