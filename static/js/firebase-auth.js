/**
 * CleanCity authentication helper.
 * - Uses the Firebase Web SDK when it is configured and available.
 * - Always falls back to the server's own e-mail/password login, so the
 *   seeded admin/staff accounts and offline demos keep working.
 * Every request carries the CSRF token from <meta name="csrf-token">.
 */
(function () {
  'use strict';

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  function jsonHeaders() {
    return { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() };
  }

  async function postJSON(url, payload) {
    const resp = await fetch(url, {
      method: 'POST',
      headers: jsonHeaders(),
      credentials: 'same-origin',
      body: JSON.stringify(payload)
    });
    let data = {};
    try { data = await resp.json(); } catch (e) { /* non-JSON error page */ }
    return data;
  }

  function getFirebaseAuth() {
    if (typeof firebase !== 'undefined' && firebase.apps && firebase.apps.length && firebase.auth) {
      return firebase.auth();
    }
    return null;
  }

  const FIREBASE_AUTH_ERRORS = {
    'auth/invalid-email': 'Please enter a valid email address.',
    'auth/user-disabled': 'This user account has been disabled by an administrator.',
    'auth/user-not-found': 'Invalid email or password.',
    'auth/wrong-password': 'Invalid email or password.',
    'auth/invalid-credential': 'Invalid email or password.',
    'auth/email-already-in-use': 'An account with this email already exists. Please sign in.',
    'auth/weak-password': 'Password should be at least 6 characters long.',
    'auth/operation-not-allowed': 'Email/Password sign-in is disabled in the Firebase Console.',
    'auth/too-many-requests': 'Too many unsuccessful attempts. Try again later.',
    'auth/network-request-failed': 'Network error. Please check your internet connection.'
  };

  function translateError(err) {
    if (!err) return 'An unknown error occurred.';
    return FIREBASE_AUTH_ERRORS[err.code] || err.message || 'Authentication failed.';
  }

  // Server-side e-mail + password login (local accounts first, Firebase REST for citizens)
  async function backendPasswordLogin(email, password, nextUrl) {
    try {
      const data = await postJSON('/api/auth/firebase-login', { email, password, next: nextUrl });
      if (data.success) return { success: true, redirect: data.redirect || '/' };
      return { success: false, error: data.error || 'Invalid email or password.' };
    } catch (e) {
      return { success: false, error: 'Network error contacting authentication server.' };
    }
  }

  async function signInWithFirebase(email, password, nextUrl) {
    const auth = getFirebaseAuth();

    if (auth) {
      try {
        const cred = await auth.signInWithEmailAndPassword(email, password);
        const idToken = await cred.user.getIdToken();
        const data = await postJSON('/api/auth/firebase-login', { idToken, next: nextUrl });
        if (data.success) return { success: true, redirect: data.redirect || '/' };
        // Token not accepted (e.g. staff/admin account): try the official password login.
      } catch (err) {
        console.info('[Firebase Auth] client sign-in not used:', err.code || err.message);
      }
    }
    return backendPasswordLogin(email, password, nextUrl);
  }

  async function registerWithFirebase(userData) {
    const auth = getFirebaseAuth();
    const { name, email, phone, password } = userData;

    if (auth) {
      try {
        const cred = await auth.createUserWithEmailAndPassword(email, password);
        try { await cred.user.updateProfile({ displayName: name }); } catch (e) { /* optional */ }
        const idToken = await cred.user.getIdToken();
        const data = await postJSON('/api/auth/firebase-register', {
          idToken, email: cred.user.email, name, phone, password
        });
        if (data.success) return { success: true, redirect: data.redirect || '/citizen/dashboard' };
        return { success: false, error: data.error || 'Failed to complete registration.' };
      } catch (err) {
        return { success: false, error: translateError(err), code: err.code };
      }
    }

    try {
      const data = await postJSON('/api/auth/firebase-register', userData);
      if (data.success) return { success: true, redirect: data.redirect || '/citizen/dashboard' };
      return { success: false, error: data.error || 'Registration failed.' };
    } catch (e) {
      return { success: false, error: 'Network error connecting to registration server.' };
    }
  }

  async function resetPasswordWithFirebase(email) {
    const auth = getFirebaseAuth();
    if (auth) {
      try {
        await auth.sendPasswordResetEmail(email);
        return { success: true, message: 'If that account exists, a password reset link has been sent.' };
      } catch (err) {
        console.info('[Firebase Auth] password reset fallback:', err.code || err.message);
      }
    }
    try {
      return await postJSON('/api/auth/forgot-password', { email });
    } catch (e) {
      return { success: false, error: 'Could not send password reset email. Please try again.' };
    }
  }

  window.CleanCityAuth = {
    signIn: signInWithFirebase,
    register: registerWithFirebase,
    resetPassword: resetPasswordWithFirebase,
    getAuth: getFirebaseAuth
  };
})();
