/**
 * Authentication API routes.
 */

import { Router } from 'express';
import { mkdir } from 'fs/promises';
import path from 'path';
import {
  loadUsersConfig, saveUsersConfig, findUser, usernameFromEmail,
  hashPassword, verifyPassword, isPasswordSet,
} from '../auth.js';

export function createAuthRouter(sessionStore, usersConfigPath, dataRoot, onLogin) {
  const router = Router();

  router.post('/login', async (req, res) => {
    const { email, password } = req.body || {};
    if (!email) return res.status(400).json({ error: 'Email is required' });

    let config;
    try {
      config = loadUsersConfig(usersConfigPath);
    } catch (err) {
      console.error('[Auth] Failed to read users config:', err.message);
      return res.status(500).json({ error: 'Server configuration error' });
    }

    const user = findUser(config, email);
    if (!user) return res.status(401).json({ error: 'Invalid credentials' });

    if (!isPasswordSet(user)) {
      return res.status(403).json({ error: 'password_not_set', message: 'Password not set. Please set your password.' });
    }

    if (!password) return res.status(401).json({ error: 'Invalid credentials' });

    const match = await verifyPassword(password, user.password_hash);
    if (!match) return res.status(401).json({ error: 'Invalid credentials' });

    const username = usernameFromEmail(user.email);

    await mkdir(path.join(dataRoot, username), { recursive: true });

    const token = sessionStore.create(username, user.email);
    if (onLogin) onLogin(username);
    res.json({ token, username, email: user.email });
  });

  router.post('/set-password', async (req, res) => {
    const { email, password } = req.body || {};
    if (!email || !password) {
      return res.status(400).json({ error: 'Email and password are required' });
    }
    if (password.length < 6) {
      return res.status(400).json({ error: 'Password must be at least 6 characters' });
    }

    let config;
    try {
      config = loadUsersConfig(usersConfigPath);
    } catch (err) {
      console.error('[Auth] Failed to read users config:', err.message);
      return res.status(500).json({ error: 'Server configuration error' });
    }

    const user = findUser(config, email);
    if (!user) return res.status(401).json({ error: 'Invalid credentials' });

    if (isPasswordSet(user)) {
      return res.status(400).json({ error: 'Password is already set. Contact admin to reset.' });
    }

    user.password_hash = await hashPassword(password);

    try {
      saveUsersConfig(usersConfigPath, config);
    } catch (err) {
      console.error('[Auth] Failed to write users config:', err.message);
      return res.status(500).json({ error: 'Failed to save password' });
    }

    const username = usernameFromEmail(user.email);
    await mkdir(path.join(dataRoot, username), { recursive: true });

    const token = sessionStore.create(username, user.email);
    console.log(`[Auth] Password set for ${email} (username=${username})`);
    if (onLogin) onLogin(username);
    res.json({ token, username, email: user.email });
  });

  router.post('/logout', (req, res) => {
    const authHeader = req.headers.authorization;
    if (authHeader?.startsWith('Bearer ')) {
      sessionStore.revoke(authHeader.slice(7));
    }
    res.json({ ok: true });
  });

  router.get('/me', (req, res) => {
    const authHeader = req.headers.authorization;
    if (!authHeader?.startsWith('Bearer ')) {
      return res.status(401).json({ error: 'Not authenticated' });
    }

    const session = sessionStore.validate(authHeader.slice(7));
    if (!session) return res.status(401).json({ error: 'Session expired' });

    res.json({ username: session.username, email: session.email });
  });

  return router;
}
