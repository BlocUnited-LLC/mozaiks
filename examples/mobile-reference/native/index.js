import { App } from '@capacitor/app';
import { Browser } from '@capacitor/browser';
import { Capacitor } from '@capacitor/core';
import { createAuthAdapter as createSharedAuthAdapter } from '@mozaiks/chat-ui/auth';
import { createNativeAppAuthAdapter } from './authAdapter.mjs';

export const createAuthAdapter = options => createNativeAppAuthAdapter(options, {
  App, Browser, createSharedAuthAdapter, platform: Capacitor.getPlatform(), window,
});
