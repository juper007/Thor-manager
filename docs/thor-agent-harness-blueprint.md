# Thor Agent Harness 청사진

상태: Draft  
대상 프로젝트: Thor Monitor  
목표 플랫폼: NVIDIA Jetson Thor / JetPack 7.2  
주 추론 모델: Qwen3.8-27B TensorRT Edge-LLM

실행 순서와 진행 상태는 [`thor-agent-harness-execution-plan.md`](thor-agent-harness-execution-plan.md)에서 관리한다.

## 1. 목적과 원칙

Thor Agent Harness는 Thor Monitor의 AI Workspace에서 자연어 요청을 받아 계획을 세우고, 허용된 도구를 실행하고, 결과를 검증해 완료까지 진행하는 독립형 코딩 에이전트 런타임이다.

Claude Code를 포함한 기존 에이전트 제품과 비슷한 사용자 경험을 목표로 할 수 있지만, 유출되거나 비공개인 독점 코드는 사용하지 않는다. 공개 문서와 일반적인 에이전트 설계 패턴만 사용해 clean-room 방식으로 구현한다.

핵심 원칙은 다음과 같다.

- Qwen3.8-27B를 기본 추론 엔진으로 사용한다.
- 모델은 시스템을 직접 조작하지 않고 등록된 도구만 호출한다.
- 읽기 작업은 기본적으로 자동 허용한다.
- 쓰기, 삭제, 외부 전송, 시스템 변경은 위험도에 따라 승인받는다.
- 모든 실행과 승인, 파일 변경, 검증 결과를 기록한다.
- 작업 폴더 밖의 접근과 비밀정보 유출을 기본적으로 차단한다.
- 모델과 UI를 교체해도 사용할 수 있도록 에이전트 코어를 독립 모듈로 만든다.

## 2. 대표 실행 흐름

```text
사용자 요청
   ↓
요청 분석과 계획 수립
   ↓
필요한 도구 선택
   ↓
권한 정책 확인
   ↓
파일·셸·Git·웹·Python 실행
   ↓
결과 관찰 및 계획 수정
   ↓
테스트·검증
   ↓
변경 내역과 최종 답변
```

## 3. 전체 아키텍처

```text
Thor Monitor AI Workspace
          │
          │ HTTP / WebSocket
          ▼
┌─────────────────────────────┐
│ Agent API                   │
│ 세션·스트리밍·취소·승인     │
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│ Agent Runtime               │
│                             │
│ Planner → Tool Loop → Judge │
│    ↑          ↓             │
│ Context   Tool Results      │
└──────┬────────┬────────┬────┘
       │        │        │
       ▼        ▼        ▼
 Permission   Memory    Skills
   Engine     Store     Loader
       │
       ▼
┌─────────────────────────────┐
│ Tool Registry               │
├─────────────────────────────┤
│ File / Search / Patch       │
│ Shell / Python / Git        │
│ Web / System / MCP          │
│ Test / Lint / Build         │
└──────────────┬──────────────┘
               ▼
       Sandbox / Workspace
               │
               ▼
        Jetson Thor Host
```

## 4. Agent Runtime

현재의 단순 반복형 `agent_chat()`을 명시적인 상태 머신으로 교체한다.

| 상태 | 의미 |
|---|---|
| `analyzing` | 사용자 요청과 범위 분석 |
| `planning` | 실행 계획 작성 |
| `awaiting_approval` | 위험 작업 승인 대기 |
| `executing` | 도구 실행 |
| `observing` | 도구 결과 해석 |
| `verifying` | 테스트와 결과 검증 |
| `completed` | 작업 완료 |
| `failed` | 복구 불가능한 오류 |
| `cancelled` | 사용자 취소 |

각 실행에는 고유한 `run_id`를 부여한다. 브라우저를 새로고침하거나 서버가 재시작되어도 SQLite에 저장된 상태로 세션을 복구할 수 있어야 한다.

에이전트 루프에는 다음 제한을 둔다.

- 최대 도구 호출 수
- 최대 반복 횟수
- 전체 실행 제한 시간
- 동일 도구 호출 중복 방지
- 반복 실패 감지
- 사용자 취소 신호
- 모델 응답과 도구 결과 크기 제한

## 5. Tool Registry

모든 도구는 공통 인터페이스와 JSON Schema를 사용한다.

```python
class Tool:
    name: str
    description: str
    input_schema: dict
    risk_level: str

    def validate(self, arguments): ...
    def authorize(self, context, arguments): ...
    def execute(self, context, arguments): ...
```

초기 도구 목록:

- `file_read`
- `file_list`
- `file_search`
- `file_write`
- `file_patch`
- `shell_execute`
- `python_execute`
- `git_status`
- `git_diff`
- `git_commit`
- `web_search`
- `web_fetch`
- `system_status`
- `test_run`

Qwen이 생성하는 비표준 tool-call 형식은 현재의 tolerant parser를 확장해 처리하되, 실행 전에는 반드시 스키마 검증과 권한 검사를 통과해야 한다.

## 6. Permission Engine

| 등급 | 작업 예시 | 기본 정책 |
|---|---|---|
| Read | 파일 읽기, 검색, 상태 확인 | 자동 허용 |
| Safe write | 작업 폴더 내부 파일 생성·수정 | 세션 설정에 따라 허용 |
| Elevated | 패키지 설치, 서비스 재시작, 외부 네트워크 | 승인 필요 |
| Destructive | 삭제, 강제 Git 변경, 시스템 설정 | 항상 승인 |

필수 보호 장치:

- resolve된 경로가 허용된 작업 폴더 내부인지 확인
- 심볼릭 링크와 `..`를 통한 경로 탈출 차단
- 명령별 시간, 메모리, CPU, PID, 출력 제한
- 로그와 UI에서 비밀번호, 토큰, API 키 자동 마스킹
- 위험한 명령과 인자 조합 별도 탐지
- 외부 메시지 전송과 네트워크 변경은 명시적 승인
- 웹 요청의 내부 IP, 리다이렉트, DNS 재해석 보호
- 승인 시 실행 대상과 예상 영향을 사용자에게 표시

## 7. Workspace Manager

한 세션은 하나의 명시적인 작업 폴더와 연결된다.

```json
{
  "workspace": "/home/juper007/projects/example",
  "run_id": "run_...",
  "branch": "agent/task-name",
  "permissions": {
    "read": "auto",
    "write": "ask",
    "shell": "ask"
  }
}
```

Git 저장소에서는 선택적으로 별도 worktree와 `agent/` 접두사 브랜치를 만들어 사용자 작업과 에이전트 변경을 격리한다. 기존 미커밋 변경은 사용자 소유로 간주하고 임의로 덮어쓰거나 되돌리지 않는다.

## 8. Context Manager

64K 컨텍스트는 다음 우선순위로 구성한다.

1. 현재 사용자 요청
2. 활성 계획과 현재 단계
3. 최근 도구 실행 결과
4. 수정하거나 검토 중인 파일 일부
5. 프로젝트 지침과 활성 스킬
6. 이전 대화 요약
7. 장기 메모리

큰 파일은 통째로 삽입하지 않고 검색 결과와 필요한 범위만 사용한다. 오래된 도구 출력은 요약하며 원문은 세션 저장소에 보관한다. 모델이 만든 요약과 실제 도구 결과는 명확하게 구분한다.

## 9. Skills

기존 `skills/*/SKILL.md` 구조를 유지하면서 다음 항목을 추가로 정의할 수 있게 한다.

- 사용할 수 있는 도구 목록
- 필요한 승인 등급
- 실행 전후 검증 절차
- 참고 문서와 지원 스크립트
- 완료 조건과 실패 처리

예상 기본 스킬:

- `code-interpreter`
- `code-review`
- `debugging`
- `test-and-fix`
- `git-workflow`
- `web-research`
- `dependency-audit`
- `docker-deployment`
- `systemd-service`
- `jetson-optimization`

## 10. Session과 Memory

초기 저장소는 SQLite를 사용한다.

저장 대상:

- 대화 메시지
- 계획과 단계별 상태
- 도구 호출, 인자, 결과
- 사용자 승인과 거부 기록
- 실행 시간과 모델 사용량
- 수정 파일과 Git diff
- 테스트 및 검증 결과
- 최종 작업 요약
- 사용자 및 프로젝트별 메모리

세션 재개, 이전 작업 검색, 실패 지점부터 재시도, 오래된 세션 정리를 지원한다. 비밀정보와 대용량 바이너리는 데이터베이스에 직접 저장하지 않는다.

## 11. AI Workspace UI

### 작업 패널

- 현재 계획과 단계
- 완료, 진행, 대기 상태
- 경과 시간과 현재 모델
- 작업 중단 버튼

### 도구 실행 카드

```text
◈ FILE PATCH
server.py 수정
+28 / -7 lines
[변경 보기]
```

### 승인 화면

```text
승인이 필요한 작업

sudo systemctl restart thor-monitor.service

영향:
- Thor Monitor가 약 2~5초 중단됩니다.

[한 번 허용] [이 유형 항상 허용] [거부]
```

### 변경 검토

- 파일별 diff
- 변경 전후 비교
- 테스트 결과
- 승인, 되돌리기, Git 커밋

### 실행 모드

| 모드 | 동작 |
|---|---|
| Ask | 답변만 제공 |
| Plan | 분석과 계획까지만 작성 |
| Agent | 승인 정책 안에서 실제 작업 수행 |
| Autonomous | 사전 승인된 안전 범위에서 완료까지 수행 |

첫 릴리스에는 `Ask`, `Plan`, `Agent`만 제공한다.

## 12. 예상 디렉터리 구조

```text
thor-monitor/
├── server.py
├── agent/
│   ├── runtime.py
│   ├── state.py
│   ├── planner.py
│   ├── context.py
│   ├── permissions.py
│   ├── sessions.py
│   ├── models.py
│   └── protocol.py
├── tools/
│   ├── base.py
│   ├── registry.py
│   ├── files.py
│   ├── search.py
│   ├── patch.py
│   ├── shell.py
│   ├── python.py
│   ├── git.py
│   ├── web.py
│   └── system.py
├── skills/
│   ├── code-interpreter/
│   ├── code-review/
│   ├── debugging/
│   └── deployment/
├── sandbox/
│   ├── docker.py
│   ├── paths.py
│   └── limits.py
├── storage/
│   ├── database.py
│   ├── migrations/
│   └── redaction.py
├── web/
│   ├── agent-runtime.js
│   ├── agent-tools.js
│   ├── agent-diff.js
│   └── agent.css
└── tests/
    ├── test_runtime.py
    ├── test_permissions.py
    ├── test_paths.py
    ├── test_tool_calls.py
    └── test_agent_e2e.py
```

## 13. 구현 로드맵

### 1단계: 실행 엔진

- 기존 에이전트 코드를 모듈화
- 표준 Tool Registry 구현
- 상태 기반 agent loop
- 스트리밍 이벤트 프로토콜
- 취소, 타임아웃, 중복 실행 방지
- SQLite 세션 저장

완료 기준: 계산, 웹 검색, 시스템 상태 도구를 연속 호출하고 정상적인 최종 답변과 실행 기록을 생성한다.

### 2단계: 코딩 도구

- 작업 폴더 선택
- 파일 읽기와 검색
- 안전한 patch 적용
- 격리된 셸 실행
- Git status와 diff
- 테스트 실행

완료 기준: 작은 버그 수정 요청을 받아 파일 수정, 테스트, 결과 설명까지 수행한다.

### 3단계: 권한과 격리

- 위험도 분류
- 승인 API와 UI
- Docker 실행 샌드박스
- 경로 및 심볼릭 링크 보호
- 비밀정보 마스킹
- Git worktree 지원

완료 기준: 작업 폴더 밖 쓰기와 위험 명령이 승인 없이 실행되지 않는다.

### 4단계: UI 완성

- 계획과 진행 상태
- 실시간 도구 실행 카드
- 로그와 오류 표시
- diff viewer
- 승인 다이얼로그
- 세션 중단과 재개

### 5단계: Skills와 MCP

- 스킬 자동 탐색
- 스킬별 허용 도구와 검증 절차
- MCP 클라이언트
- 외부 MCP 서버 연결
- 프로젝트별 사용자 지침

### 6단계: 고급 기능

- 컨텍스트 자동 압축
- 장기 메모리
- 반복 실패 감지와 복구
- 검증 전담 하위 에이전트
- 예약 작업과 원격 알림

## 14. 첫 릴리스의 기준 시나리오

사용자 요청:

> 이 프로젝트를 분석하고 로그인 오류를 수정한 뒤 테스트해줘.

필수 실행 흐름:

1. 저장소와 작업 폴더 상태 확인
2. 관련 파일 검색
3. 원인 분석
4. 실행 계획 표시
5. 쓰기 작업 승인 요청
6. patch 적용
7. 테스트 실행
8. 실패 시 원인 분석과 제한된 재시도
9. Git diff 표시
10. 최종 결과와 남은 위험 요약

이 시나리오가 안정적으로 동작한 뒤 MCP, 하위 에이전트, 자동 배포를 추가한다.

## 15. 결정 기록

- 구현 순서: `Runtime → Tools → Permissions → Sessions → UI → Skills/MCP`
- 초기 백엔드: Python
- 초기 저장소: SQLite
- 초기 UI: 기존 Thor Monitor AI Workspace 확장
- 초기 실행 모드: Ask, Plan, Agent
- 기본 AI 동시 실행 수: 1
- 기본 정책: 읽기 자동 허용, 쓰기 및 시스템 변경 승인
- 구현 방식: 독점 또는 유출 코드 없이 clean-room 독립 구현
