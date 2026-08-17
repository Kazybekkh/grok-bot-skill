# Security

This skill talks to Grok Bot using the signed-in desktop session on your Mac.

- Never commit `sand-secrets.json`, Keychain dumps, JWTs, or gateway URLs.
- The CLI must not print tokens. If you see a token in output, treat it as leaked and sign in again.
- Do not send passwords, API keys, or 2FA codes in Grok Bot prompts. Use **Agent Computer** takeover in the app.
- All Grok Bots on an account share one cloud computer. Do not treat bot names as isolation.

Report issues at https://github.com/adamanz/grok-bot-skill/issues.
