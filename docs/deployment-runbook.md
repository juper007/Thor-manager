# Thor Monitor 배포 런북

이 문서는 Windows 작업 폴더의 Thor Monitor를 Jetson Thor 서버에 안전하고 반복 가능하게 배포하는 표준 절차다.

## 고정 배포 정보

| 항목 | 값 |
|---|---|
| SSH 대상 | `jetsonthor` |
| 서버 hostname | `jetsonthor01` |
| 서버 IP | `192.168.1.4` |
| 서버 사용자 | `juper007` |
| 배포 디렉터리 | `/home/juper007/thor-monitor` |
| systemd unit | `/etc/systemd/system/thor-monitor.service` |
| 서비스 이름 | `thor-monitor.service` |
| 웹 주소 | `http://192.168.1.4:8090` |
| 세션 DB 기본 경로 | `/home/juper007/thor-monitor/data/sessions.db` |

현재 서버의 배포 디렉터리는 Git checkout이 아니다. 서버에서 `git pull`하지 말고 아래의 Git archive 방식으로 배포한다.

## 절대 덮어쓰거나 전송하지 않을 파일

- `thor-monitor.env`: 비밀번호와 API 키가 들어 있는 서버 전용 파일
- `data/`: SQLite 세션 데이터
- `generated/`: 생성 이미지와 이력
- `qwen-image/qwen-image.env`: 이미지 서비스 API 키
- 모델 및 엔진 파일

이 파일들은 `.gitignore`에 포함되므로 `git archive`에는 들어가지 않는다. 임의의 `scp -r`로 작업 폴더 전체를 복사하지 않는다.

## 1. 로컬 사전 확인

PowerShell에서 프로젝트 폴더로 이동한다.

```powershell
Set-Location 'C:\Users\juper\OneDrive\Documents\ChatGPT\Thor Monitor'
git status --short
git log -1 --oneline
ssh jetsonthor "hostname; systemctl is-active thor-monitor.service"
```

예상 결과:

- Git 작업 트리가 깨끗하거나, 배포하려는 변경만 표시된다.
- hostname은 `jetsonthor01`이다.
- 서비스 상태는 `active`다.

커밋하지 않은 변경까지 배포해야 하는 특별한 경우가 아니라면 먼저 변경을 커밋한다. `git archive HEAD`는 커밋된 파일만 포함한다.

## 2. 배포 패키지 생성 및 전송

Git이 추적하는 파일만 하나의 archive로 만든다. 이 방법은 여러 파일을 한 디렉터리로 `scp`하여 `runtime.py`, `state.py`, 테스트 파일이 프로젝트 루트에 잘못 생성되는 문제를 방지한다.

```powershell
git archive --format=tar -o "$env:TEMP\thor-monitor-deploy.tar" HEAD
scp "$env:TEMP\thor-monitor-deploy.tar" jetsonthor:/tmp/thor-monitor-deploy.tar
```

archive 내용을 배포 디렉터리에 푼다.

```powershell
ssh jetsonthor "tar -xf /tmp/thor-monitor-deploy.tar -C /home/juper007/thor-monitor"
```

Workspace Git 도구가 작동하려면 배포 디렉터리에 로컬 Git 기준선이 있어야 한다. 최초 한 번 `git init` 후 비밀·운영 데이터가 `.gitignore`로 제외되는지 확인하고 기준선 커밋을 만든다. 이후 archive 배포가 끝날 때마다 배포된 추적 파일만 새 로컬 기준선으로 커밋한다. 운영 `thor-monitor.env`, `data/`, 생성 이미지, DB는 절대 stage하지 않는다.

배포 후 다음 파일이 프로젝트 루트에 생겼다면 잘못 배포한 것이다.

```text
runtime.py
state.py
test_server.py
test_runtime_state.py
test_storage.py
```

정상 위치는 각각 `agent/`와 `tests/` 아래다.

## 3. 운영 데이터와 설정 확인

서버에 접속해서 확인한다. 비밀번호나 API 키 값 자체를 출력하지 않는다.

```powershell
ssh jetsonthor
```

이후 서버 셸에서 실행한다.

```bash
cd /home/juper007/thor-monitor
test -s thor-monitor.env
test -d data
test -f storage/migrations/001_initial.sql
grep -q '^THOR_MONITOR_PASSWORD=' thor-monitor.env
```

세션 DB가 이미 있으면 재시작 전에 SQLite online backup을 만든다.

```bash
mkdir -p /home/juper007/thor-monitor-backups
python3 -c "import sqlite3,time; src=sqlite3.connect('data/sessions.db'); dst=sqlite3.connect('/home/juper007/thor-monitor-backups/sessions-'+time.strftime('%Y%m%d-%H%M%S')+'.db'); src.backup(dst); dst.close(); src.close()" 
chmod 600 data/sessions.db
```

DB가 아직 없으면 backup 명령은 생략한다. `thor-monitor.env`는 항상 `600`을 유지한다.

```bash
chmod 600 thor-monitor.env
```

## 4. 운영 DB와 분리해서 테스트

테스트에서 `server.py`를 import하면 세션 저장소가 초기화된다. 반드시 임시 DB를 지정하여 운영 `data/sessions.db`를 테스트 데이터로 오염시키지 않는다.

서버 셸에서 실행한다.

```bash
cd /home/juper007/thor-monitor
deploy_test_dir=$(mktemp -d)
THOR_SESSION_DB="$deploy_test_dir/sessions.db" PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile server.py agent/runtime.py agent/state.py storage/database.py storage/redaction.py
THOR_SESSION_DB="$deploy_test_dir/sessions.db" PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
rm -rf -- "$deploy_test_dir"
```

현재 기준은 전체 테스트 165개 통과다. 한 개라도 실패하면 서비스를 재시작하지 않는다.

## 5. 서비스 재시작

정식 방법:

```bash
sudo systemctl daemon-reload
sudo systemctl restart thor-monitor.service
systemctl is-active thor-monitor.service
systemctl status thor-monitor.service --no-pager -l
```

unit 파일이 변경되지 않았다면 `daemon-reload`는 생략할 수 있다.

sudo를 사용할 수 없고 현재 unit의 `Restart=always`가 확실한 경우에만 다음 방법을 사용한다.

```bash
monitor_pid=$(systemctl show thor-monitor.service -p MainPID --value)
test "$monitor_pid" -gt 1
kill "$monitor_pid"
sleep 5
systemctl is-active thor-monitor.service
```

`kill` 대상 PID가 1 이하라면 중단하고 실행하지 않는다.

## 6. 배포 검증

채팅 스트리밍은 `stream:true`로 요청해 `start` 다음에 여러 `delta`, 마지막에 정확히 한 개의 `final` 이벤트가 오는지 확인한다. `curl -N`은 응답 버퍼링을 끄므로 실제 점진 전송 여부도 확인할 수 있다.

```bash
set -a
. ./thor-monitor.env
set +a
curl -N --max-time 240 -sS -u "thor:$THOR_MONITOR_PASSWORD" \
  -H 'Content-Type: application/json' \
  --data '{"run_id":"stream-smoke","stream":true,"messages":[{"role":"user","content":"짧은 문장 다섯 개로 답해줘."}]}' \
  http://127.0.0.1:8090/api/chat
```

`delta`에 내부 tool-call JSON이나 `<tool_call>` 마크업이 노출되면 실패다. 도구 실행 결과와 전체 최종 답변은 `final`에서 확인한다.

서버 셸에서 환경 파일을 로드하되 값을 출력하지 않는다.

```bash
cd /home/juper007/thor-monitor
set -a
. ./thor-monitor.env
set +a
curl -fsS -u "thor:$THOR_MONITOR_PASSWORD" http://127.0.0.1:8090/api/health
curl -fsS -u "thor:$THOR_MONITOR_PASSWORD" http://127.0.0.1:8090/api/chat/models
curl -fsS -u "thor:$THOR_MONITOR_PASSWORD" 'http://127.0.0.1:8090/api/chat/sessions?limit=1&offset=0'
```

SQLite 마이그레이션 버전을 확인한다.

```bash
python3 -c "import sqlite3; db=sqlite3.connect('data/sessions.db'); print(db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]); db.close()"
```

현재 예상 버전은 `5`다.

읽기 전용 Workspace 도구를 사용할 배포에서는 `thor-monitor.env`에 허용 루트를 명시한다. 여러 루트는 Linux 경로 구분자인 `:`로 구분한다.

```bash
THOR_WORKSPACE_ROOTS=/home/juper007/thor-monitor
THOR_APPROVAL_TTL_SECONDS=300
```

Workspace 목록·읽기·검색은 숨김 파일, Git 무시 파일, `.env`, SQLite DB, 키 파일과 `data`, `generated`, 캐시 디렉터리를 노출하지 않는다.

승인 대기 목록과 결정 API를 확인한다.

```bash
curl -fsS -u "thor:$THOR_MONITOR_PASSWORD" 'http://127.0.0.1:8090/api/chat/approvals?status=pending'
curl -fsS -u "thor:$THOR_MONITOR_PASSWORD" -H 'Content-Type: application/json' \
  --data '{"decision":"allow","scope":"once"}' \
  "http://127.0.0.1:8090/api/chat/approvals/APPROVAL_ID"
```

`scope`은 `once`, `session`, `always_tool` 중 하나다. 운영 환경에서는 영향 범위를 확인한 뒤 가능한 한 `once`를 사용한다.

영구 grant를 확인하고 철회할 수 있다.

```bash
curl -fsS -u "thor:$THOR_MONITOR_PASSWORD" http://127.0.0.1:8090/api/chat/permission-grants
curl -fsS -u "thor:$THOR_MONITOR_PASSWORD" -X DELETE \
  http://127.0.0.1:8090/api/chat/permission-grants/GRANT_ID
```

실제 Qwen 및 세션 저장을 확인하려면 충돌하지 않는 run ID로 요청한다.

```bash
smoke_id="deploy-$(date +%Y%m%d-%H%M%S)"
curl -fsS -u "thor:$THOR_MONITOR_PASSWORD" -H 'Content-Type: application/json' \
  --data "{\"run_id\":\"$smoke_id\",\"messages\":[{\"role\":\"user\",\"content\":\"계산기로 12 곱하기 34를 계산해줘\"}]}" \
  http://127.0.0.1:8090/api/chat
curl -fsS -u "thor:$THOR_MONITOR_PASSWORD" "http://127.0.0.1:8090/api/chat/sessions/$smoke_id"
```

최종 확인 항목:

- 서비스가 `active`
- `/api/health`가 HTTP 200
- Qwen 응답이 정상
- `tools_used`에 `calculator`가 기록됨
- 세션 상세에 사용자·assistant 메시지, 이벤트, 도구 결과가 있음
- `data/sessions.db` 소유자가 `juper007`이고 권한이 `600`

```bash
stat -c '%U:%G %a %n' data/sessions.db
```

## 7. 장애 확인

서비스가 시작되지 않으면 다음 로그부터 확인한다.

```bash
systemctl status thor-monitor.service --no-pager -l
journalctl -u thor-monitor.service -n 100 --no-pager
ss -ltnp | grep -E ':8090|:8080|:8188'
```

대표적인 실패 원인:

| 증상 | 원인 및 조치 |
|---|---|
| `ModuleNotFoundError: storage` | `storage/`와 `storage/migrations/`가 배포되지 않음 |
| `no such table: sessions` | migration 파일 누락 또는 잘못된 `THOR_SESSION_DB` |
| HTTP 503 | `thor-monitor.env` 누락 또는 `THOR_MONITOR_PASSWORD` 미설정 |
| HTTP 429 | 기존 모델 요청이 아직 실행 중임. 완료 후 재시도 |
| SSH host key 오류 | JetPack 재설치 후 기존 known_hosts 항목을 확인하고 새 fingerprint를 검증한 뒤 갱신 |
| 브라우저에 이전 UI 표시 | 강력 새로고침 후 서버의 실제 URL `http://192.168.1.4:8090`인지 확인 |
| 테스트 후 운영 DB에 테스트 세션 생성 | 테스트 시 `THOR_SESSION_DB` 임시 경로를 지정하지 않음 |

## 8. 롤백 원칙

1. 현재 오류 로그와 DB 파일을 먼저 보존한다.
2. 이전 정상 커밋으로 새 deploy archive를 만든다.
3. 같은 archive 배포 절차로 코드만 되돌린다.
4. DB schema가 변경된 배포라면 코드와 함께 만든 DB backup을 사용한다.
5. `thor-monitor.env`, `generated/`, 모델 파일은 롤백 과정에서 삭제하거나 덮어쓰지 않는다.

이전 커밋 archive 예시:

```powershell
git archive --format=tar -o "$env:TEMP\thor-monitor-rollback.tar" <정상-커밋-해시>
scp "$env:TEMP\thor-monitor-rollback.tar" jetsonthor:/tmp/thor-monitor-deploy.tar
ssh jetsonthor "tar -xf /tmp/thor-monitor-deploy.tar -C /home/juper007/thor-monitor"
```

DB 복원은 서비스 정지와 현재 DB 보존이 필요한 파괴적 작업이므로, 정확한 backup 파일을 확인한 뒤 별도 승인을 받고 수행한다.

## 배포 완료 기록 양식

```text
배포 커밋:
배포 일시:
자동 테스트:
서비스 상태:
API health:
실제 Qwen smoke:
SQLite schema:
재시작 후 세션 조회:
GitHub push:
특이사항:
```
