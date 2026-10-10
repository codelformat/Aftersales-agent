/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_LANGFUSE_URL?: string;
  readonly VITE_DATA_SOURCE?: 'live' | 'replay';
}
