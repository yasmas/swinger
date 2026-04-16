/**
 * Authentication module — session store, password hashing, users.yaml I/O.
 */

import { readFileSync, writeFileSync } from 'fs';
import { randomBytes } from 'crypto';
import bcrypt from 'bcryptjs';
import YAML from 'yaml';

const BCRYPT_ROUNDS = 10;

// ── Users YAML I/O ──────────────────────────────────────────────────

export function loadUsersConfig(filePath) {
  const raw = readFileSync(filePath, 'utf8');
  return YAML.parse(raw) || {};
}

export function saveUsersConfig(filePath, config) {
  const doc = new YAML.Document(config);
  writeFileSync(filePath, doc.toString(), 'utf8');
}

export function findUser(usersConfig, email) {
  return (usersConfig.users || []).find(
    u => u.email.toLowerCase() === email.toLowerCase()
  );
}

export function usernameFromEmail(email) {
  return email.split('@')[0].toLowerCase();
}

// ── Password helpers ────────────────────────────────────────────────

export async function hashPassword(plain) {
  return bcrypt.hash(plain, BCRYPT_ROUNDS);
}

export async function verifyPassword(plain, hash) {
  return bcrypt.compare(plain, hash);
}

export function isPasswordSet(user) {
  return user.password_hash && user.password_hash.length > 0;
}

// ── Session Store ───────────────────────────────────────────────────

export class SessionStore {
  constructor(ttlHours = 24) {
    this.ttlMs = ttlHours * 60 * 60 * 1000;
    /** @type {Map<string, {username: string, email: string, admin: boolean, expiresAt: number}>} */
    this._tokens = new Map();
  }

  setTTL(hours) {
    this.ttlMs = hours * 60 * 60 * 1000;
  }

  create(username, email, admin = false) {
    const token = randomBytes(32).toString('hex');
    this._tokens.set(token, {
      username,
      email,
      admin: !!admin,
      expiresAt: Date.now() + this.ttlMs,
    });
    return token;
  }

  validate(token) {
    const session = this._tokens.get(token);
    if (!session) return null;
    if (Date.now() > session.expiresAt) {
      this._tokens.delete(token);
      return null;
    }
    return {
      username: session.username,
      email: session.email,
      admin: !!session.admin,
    };
  }

  revoke(token) {
    this._tokens.delete(token);
  }

  cleanup() {
    const now = Date.now();
    for (const [token, session] of this._tokens) {
      if (now > session.expiresAt) this._tokens.delete(token);
    }
  }
}
