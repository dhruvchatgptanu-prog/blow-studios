# Claude Code transfer

Installs the plugins and skills from your claude.ai cloud sessions into
Claude Code on your Mac.

```bash
cd ~/blow-studios && git pull
bash claude-transfer/install.sh
```

To also install your own skills, download each one as a `.zip` from
claude.ai (Settings, Capabilities, Skills) into `~/Downloads`, then run:

```bash
bash claude-transfer/install.sh ~/Downloads
```

`plugins.txt` lists every plugin the script installs. A few plugins are only
in the claude.ai directory; the script prints those at the end so you can add
them from `/plugin` inside Claude Code.
