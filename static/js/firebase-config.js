// Firebase Web SDK initialisation for CleanCity.
// The configuration is injected by the server (from .env) as window.__FIREBASE_CONFIG__,
// so no keys are hard-coded in this file.
(function () {
  'use strict';
  const cfg = window.__FIREBASE_CONFIG__;
  window.firebaseConfig = cfg || {};

  if (!cfg || !cfg.apiKey || typeof firebase === 'undefined') {
    return; // Firebase not configured: the app falls back to server-side login.
  }
  if (!firebase.apps || !firebase.apps.length) {
    try {
      firebase.initializeApp(cfg);
    } catch (err) {
      console.warn('[Firebase] Initialization error:', err);
    }
  }
})();
