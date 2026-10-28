import type { CapacitorConfig } from '@capacitor/cli';
const config: CapacitorConfig = { appId: 'com.voicebridge.app', appName: '随声译', webDir: 'dist', android: { allowMixedContent: false }, server: { androidScheme: 'https' } };
export default config;
