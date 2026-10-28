export type RecordingHandle = { stop: () => Promise<Blob>; stream: MediaStream; analyser: AnalyserNode; close: () => void };
export async function beginRecording(): Promise<RecordingHandle> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(type => MediaRecorder.isTypeSupported(type));
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined); const chunks: BlobPart[] = [];
  const context = new AudioContext(); const source = context.createMediaStreamSource(stream); const analyser = context.createAnalyser(); analyser.fftSize = 2048; source.connect(analyser);
  recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); }; recorder.start(500);
  return { stream, analyser, stop: () => new Promise(resolve => { recorder.onstop = () => resolve(new Blob(chunks, { type: recorder.mimeType || 'audio/webm' })); recorder.stop(); }), close: () => { stream.getTracks().forEach(track => track.stop()); void context.close(); } };
}
