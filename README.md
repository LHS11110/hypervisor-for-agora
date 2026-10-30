# hypervisor-for-agora

Cloud Hypervisor **v54.0**, edk2 UEFI **ch-97eeb7b09** 기반의 Linux x86_64/KVM 환경입니다.
기본 실행은 전용 `agora-vmm` 계정과 systemd 격리 서비스를 사용합니다.

## 준비

일반 작업 계정으로 바이너리와 게스트 이미지를 준비합니다.

```bash
sudo apt-get install curl python3 gpgv ubuntu-cloudimage-keyring qemu-utils cloud-image-utils
./scripts/hypervisor setup
./scripts/hypervisor doctor
./scripts/hypervisor prepare "$HOME/.ssh/id_ed25519.pub"
```

`prepare`는 Ubuntu 24.04 LTS **release-20261017** 이미지를 내려받고,
Canonical GPG 서명·서명 키 fingerprint·고정 SHA256을 모두 검증한 뒤
10 GiB raw 디스크와 cloud-init seed를 만듭니다. 실패하면 변환하지 않습니다.
키가 없다면 `ssh-keygen -t ed25519`로 생성한 뒤 공개키 `.pub` 경로를 전달하세요.
새 게스트의 `agora` 계정은 공개키 로그인만 허용하며 sudo 권한은 없습니다.
기존 이미지의 계정 설정은 소급 변경하지 않습니다.

## 격리 서비스 설치와 실행

```bash
sudo ./scripts/managed-hypervisor install
sudo ./scripts/managed-hypervisor import
sudo ./scripts/hypervisor run
sudo ./scripts/hypervisor ping
```

`install`은 로그인 불가 시스템 계정 `agora-vmm`, root 소유 바이너리와 최소 root 파일시스템,
service/socket unit, 전용 journald 설정을 설치합니다. 현재 호스트에는 이미 설치했습니다.
자동 부팅은 활성화하지 않습니다. `import`는 `.local/guest.raw`와 `seed.img`만 복사하고
실제 디스크 공간을 미리 확보합니다. 최대 10 GiB의 여유 공간이 필요하며 기존 게스트는 덮어쓰지 않습니다.
다른 준비 디렉터리는 `sudo ./scripts/managed-hypervisor import /absolute/state/path`로 지정합니다.

기본 게스트는 **vCPU 2개 / RAM 1024 MiB / 네트워크 없음**입니다.
호스트·외부망·클라우드 메타데이터 접근을 차단하기 위해 서비스에서는 TAP도 제공하지 않습니다.
따라서 이 구성에서는 SSH 연결이 불가능합니다. 네트워크가 필요한 워크로드는
별도의 격리 네트워크 설계와 검증이 필요합니다.
seccomp와 Landlock은 항상 활성화하고 격리 실패 시 실행하지 않습니다.

```bash
sudo ./scripts/hypervisor power-button        # 게스트에 정상 종료 요청
sudo ./scripts/managed-hypervisor status
sudo ./scripts/managed-hypervisor stop        # 필요 시 강제 중지; 데이터 유실 가능
sudo journalctl --namespace=agora-hypervisor -u agora-hypervisor.service --no-pager
```

서비스 전체의 한도는 CPU 200%, RAM 12 GiB, swap 0, 태스크 64개입니다.
디스크 읽기·쓰기는 각각 50 MiB/s와 1,000 IOPS로 제한합니다.
파일 크기는 10 GiB로 제한하고 직렬 출력은 끕니다. VMM 로그는 전용 휘발성 journal에
약 16 MiB 한도로 보관하며 재부팅 시 사라집니다. 로그 회전 때문에 한도에 소폭 오차가 생길 수 있습니다.

설치 경로:

- `/usr/local/lib/agora-hypervisor/`: root 소유 실행 파일·펌웨어·격리 root
- `/var/lib/agora-hypervisor/`: 서비스 전용 게스트 디스크
- `/run/agora-hypervisor.sock`: root 전용 API 소켓
- `/etc/systemd/system/agora-hypervisor.{service,socket}`: 서비스·소켓 설정
- `/etc/sysusers.d/agora-hypervisor.conf`, `/etc/systemd/journald@agora-hypervisor.conf`: 전용 계정·로그 설정

## 개발용 로컬 실행

기존 로컬 실행은 `./scripts/hypervisor run-dev`로 명시적으로 선택할 수 있습니다.
이 경로는 seccomp·Landlock·권한 상승 방지와 파일 검증을 적용하지만,
전용 UID/namespace/cgroup/로그 용량 격리를 제공하지 않습니다. 신뢰하지 않는 게스트는
기본 격리 서비스를 사용하세요. 격리 서비스가 실패해도 개발 모드로 자동 전환하지 않습니다.

개발 모드에서만 `AGORA_CPUS`, `AGORA_MEMORY`, `AGORA_TAP`, `AGORA_STATE_DIR`가 적용됩니다.
관리 명령은 `dev-ping`, `dev-power-button`, `dev-shutdown`입니다.
직렬 로그는 `.local/serial.log`에 기록하며 `tail -f .local/serial.log | cat -v`로 표시하세요.
TAP·호스트 방화벽은 자동 구성하지 않습니다.

## 검증

```bash
python3 -I tests/test_image_verification.py
python3 -I tests/test_security.py                  # 일반 KVM 사용자로 실행
sudo python3 -I tests/test_managed_security.py    # 설치 후 실행
```

실제 KVM/UEFI 부팅, 모든 VMM 스레드의 seccomp·권한, 전용 namespace,
호스트 파일·소켓 접근 제한, root 소유 API 소켓, 커널 cgroup 한도,
읽기 전용 root/바이너리, 서비스 종료·소켓 제거를 확인했습니다.
서명·체크섬·입력 변조 거부 테스트도 통과했습니다.
Ubuntu OS 부팅과 SSH는 아직 검증하지 않았습니다.
분석 근거와 남는 신뢰 경계는 [SECURITY.md](SECURITY.md)에 정리했습니다.

## 캔버스별 NAT 코드 실행

기존 단일 VM 서비스와 별도로 캔버스마다 독립된 Cloud Hypervisor VM을 생성합니다.
게스트에는 Python, JavaScript(Node.js), Java, C, C++, 셸이 포함됩니다. VM마다 독립된
네트워크 네임스페이스와 TAP를 구성하고 NAT로 공용 IPv4 아웃바운드를 허용합니다.
새 인바운드 연결, 호스트·사설망·메타데이터·다른 VM 접근은 차단합니다. IPv6는
비활성화하며 포트포워딩은 만들지 않습니다. 제어 요청은 기존 VSOCK을 사용합니다.
캔버스 참가자들은 동일한 `/workspace` 파일과 언어별 메모리 환경을 사용합니다.

셸 코드 객체에서 `pip install 패키지명`을 실행할 수 있습니다. 설치 위치는
`/workspace/.python`이며 Python의 검색 경로와 실행 파일 경로에 포함됩니다.
설치 파일은 VM 재시작 후에도 유지됩니다. 코드 완료 응답 전에 게스트 파일 변경을
디스크에 반영합니다. 설치도 코드 실행 시간·출력·디스크 제한을 적용받습니다.
캔버스마다 언어별 인터프리터를 재사용하므로 변수·함수·객체 변경이 코드 객체 사이와
반복 실행 사이에 유지됩니다. C/C++은 Clang의 증분 컴파일/JIT를 사용합니다.
언어가 다르면 메모리는 분리되며 파일로 데이터를 교환합니다. 실행 환경 초기화는
모든 언어의 메모리를 비우고 파일은 보존합니다. C/C++의 변경되지 않은 코드는
반복 실행 시 기존 선언과 JIT 기계어를 재사용합니다. 실행문과 main의 함수 주소를
캐시해 직접 호출하므로 같은 소스는 재파싱·재컴파일하지 않습니다. 전역 변수의
최초 초기화는 한 번만 수행해 누적된 값을 보존합니다. main 본문을 수정하면 새 버전으로
컴파일해 호출하며, 같은 셀의 변경되지 않은 전역 선언과 다른 셀의 상태는 유지합니다.
컴파일 실패 시 이전 main과 상태를 유지하므로 오류를 수정해 재시도할 수 있습니다.
main 외의 선언을 수정해 같은 이름으로 재정의하거나 JavaScript의 let/const를 재선언할
때는 환경 초기화가 필요할 수 있습니다.

커널은 UID 1000과 제한된 cgroup에서만 실행되고 요청 사이에는 동결됩니다.
종료된 셀의 자식 프로세스는 정리합니다. 시간·출력 제한이나 프로세스 종료 시
전체 메모리 환경을 초기화하고 클라이언트에 알립니다. 일반 코드 예외는 앞서
정의한 상태를 보존합니다. 15분 유휴 종료와 VM 재시작 후에는 메모리가 초기화됩니다.

호스트에서 다음을 한 번 수행합니다. 이미지 준비 단계는 패키지를 내려받으므로
호스트 인터넷이 필요합니다. 완성된 게스트의 외부 통신은 위 NAT 정책을 따릅니다.
이미지에는 준비 시점의 커널과 그에 맞는 모듈이 포함됩니다. 커널이나 언어 패키지를
갱신할 때는 별도 위치에 새 이미지를 준비한 뒤 아래의 업그레이드 명령을 사용합니다.
업그레이드는 VM을 중지하고 작업 파일을 보존하며 기존 디스크를 롤백용으로 남깁니다.
업그레이드 시 메모리 상태는 초기화되고 실행 서비스가 잠시 중단됩니다.

```bash
sudo bash runtime/prepare-guest /var/lib/agora-runtime/next
sudo bash runtime/upgrade-guest /var/lib/agora-runtime/next
```

롤백 디스크는 `/var/lib/agora-runtime/backup-<시각>`에 있습니다. 검증 후 운영자가
별도로 정리합니다. 이미지 업그레이드에는 기존 디스크와 새 이미지 복사본을 위한 여유
공간이 필요합니다.

```bash
sudo bash runtime/prepare-guest
sudo bash runtime/install
python3 -m orchestra.startup development --runtime --skip-initialize
```

`runtime/install`은 네트워크·방화벽·관리자 systemd unit과 IP forwarding 설정도
설치합니다. 기존 UFW·Docker 규칙은 유지하고 `AGORA-RUNTIME` 전용 체인만 관리합니다.
`/etc/agora-runtime-network.json`의 `blocked_ipv4`에 호스트의 공인 IP 등 추가 차단할
IPv4 주소/CIDR을 지정하고 `sudo systemctl reload agora-runtime-egress`로 반영합니다.
공인 IP가 변경되면 이 목록도 갱신합니다. VM 종료 시 전용 네임스페이스와 veth를 정리합니다.

마지막 명령은 `project-orchestra` 디렉터리에서 실행합니다. 운영 배포는
`development` 대신 `production`을 사용합니다. `--runtime`은 미리 설치된 관리자
서비스가 실행 중인지 검사하고 Spring에만 관리자 소켓을 연결합니다.

동시에 최대 4개 VM, 캔버스별 단일 실행, 기본 템플릿에서 VM당 1 GiB 메모리와 6 GiB 디스크를
사용합니다. 실행은 15초, 코드와 출력은 각각 64 KiB로 제한됩니다. 15분간
사용하지 않은 VM은 중지하지만 디스크는 보관합니다. 현재 최대 32개 캔버스
디스크를 보관하며, 캔버스를 삭제해도 디스크는 자동 삭제하지 않습니다. 디스크는
공간을 미리 할당하고 호스트에 최소 8 GiB를 남길 수 없으면 생성하지 않습니다. 운영자는
호스트 저장 용량을 감시하고 삭제된 캔버스의 디스크를 별도로 정리해야 합니다.
이 구성은 단일 호스트 기반이며 VM과 작업 공간의 고가용성이나 백업을 제공하지 않습니다.


커널 검증은 비특권 컨테이너에서 실행합니다.

```bash
python3 -m unittest discover -s runtime/tests -p 'test_*.py'
docker build -t agora-runtime-guest:local runtime/guest
docker run --rm --network none --cap-drop ALL --user 1000:1000 --read-only \
  --tmpfs /workspace:uid=1000,gid=1000 --tmpfs /tmp:uid=1000,gid=1000 \
  -v "$PWD/runtime/tests/kernels_integration.py:/tests/kernels.py:ro" \
  agora-runtime-guest:local python3 /tests/kernels.py
```


실제 배포 환경의 NAT 검증은 아래 명령을 사용합니다. 전용 임시 VM만 생성·삭제하며
DNS/TLS, pip 설치, 재시작 후 패키지 유지, 사설망 차단, 리스너가 열린 VM에 대한
호스트·외부 네임스페이스의 신규 연결 차단을 확인합니다.

```bash
sudo python3 runtime/tests/network_integration.py
```


## Git에 포함하는 파일

소스 코드, `config/`의 고정 버전·체크섬, systemd unit, 테스트와 공개 서명 fixture를
함께 관리합니다. `.gitignore`는 로컬 VM 상태, 디스크 이미지·펌웨어 다운로드,
cloud-init 생성 파일, 환경 변수 파일·개인키, 캐시·로그·빌드 산출물을 제외합니다.
`tests/fixtures/ubuntu/SHA256SUMS.gpg`는 이미지 검증에 필요한 공개 서명이므로 포함합니다.

사용자 지정 `AGORA_STATE_DIR`는 저장소 밖이나 `.local/` 아래에 두세요.
환경 변수 예제를 추가할 때는 실제 인증 정보를 제거한 `.env.example` 또는
`.env.*.example` 파일을 사용하면 Git으로 관리할 수 있습니다.


## 언어별 실행 환경 설정

캔버스 상단 **실행 환경**에서 언어별 런타임과 옵션을 설정합니다. 설정은 캔버스
전체 참가자가 공유하며 서버의 `/var/lib/agora-runtime/settings/<canvasId>.json`에
원자적으로 저장됩니다. 접근 권한은 Spring의 캔버스 참가자·비밀번호 검사를 따릅니다.
동시 수정은 revision으로 검증하며 오래된 설정으로 덮어쓰지 않습니다.
설정 적용은 해당 언어의 메모리만 초기화하고 다른 언어와 작업 파일을 보존합니다.
이미지에는 아래 런타임이 실제로 설치되어 있으며 임의 실행 경로나 인자는 허용하지 않습니다.

| 언어 | 선택 가능한 환경 | 옵션 |
| --- | --- | --- |
| C++ | Clang 19/21, GCC 14 | C++11/14/17/20/23, O0–O3, 경고, Werror, 예외, RTTI |
| C | Clang 19/21, GCC 14 | C11/17/23, O0–O3, 경고, Werror |
| Python | CPython 3.11/3.12/3.13/3.14/3.15 | 최적화, 바이트코드 저장, 경고 처리 |
| Java | Temurin JDK 17/21/25 | 대상 버전, 최대 힙, assertions, preview |
| JavaScript | Node.js 20/22/24 | strict 모드, 경고 표시 |
| Shell | Bash 5.2 | errexit, nounset, pipefail |

Clang은 증분 컴파일/JIT로 코드 객체 사이의 상태를 공유합니다. GCC는 main이 있는
독립 프로그램을 컴파일하고 실행하며 실행 간 변수는 공유하지 않습니다. 같은 소스와
옵션의 GCC 실행 파일은 재사용합니다. Java는 선택한 JDK의 JShell API를 같은 JVM에서
사용하고 변수·함수를 유지합니다. 클래스에 main이 있으면 실행합니다. release는
선택한 JDK 이하만 가능하고 preview는 JDK와 release가 같아야 합니다.

Python 3.12의 패키지 경로는 기존 `/workspace/.python`을 유지합니다. 3.11·3.13·3.14·3.15는
각각 `/workspace/.python/<버전>` 경로를 사용합니다. Shell의 python/pip는 실행 환경에서 선택한 Python 버전을 따릅니다. 버전별 패키지 경로는 자동 설정됩니다. 다중 런타임 이미지의 기본 디스크는 캔버스당 6 GiB이며 12/24 GiB로 확장할 수 있습니다.

설정 API는 `GET/PUT /api/canvases/{id}/runtime/settings`입니다. PUT은
`language`, `config`, `revision` 및 필요한 경우 `passwordGrant`를 받습니다.
실행 시 클라이언트가 보낸 설정 대신 서버에 저장된 설정을 사용합니다.

설치된 모든 엔진과 옵션의 검증:

```bash
docker run --rm --network none --cap-drop ALL --user 1000:1000 --read-only \
  --tmpfs /workspace:exec,uid=1000,gid=1000 --tmpfs /tmp:uid=1000,gid=1000 \
  -v "$PWD/runtime/tests/runtime_variants.py:/tests/variants.py:ro" \
  agora-runtime-guest:local python3 /tests/variants.py
```

실제 KVM에서 모든 버전과 설정·재시작·언어별 초기화를 검증하려면
`sudo python3 runtime/tests/profiles_integration.py`를 실행합니다. 예약된 테스트 ID
2147483005가 비어 있는지 확인하고 테스트 VM과 설정 파일만 정리합니다.

## VM 리소스 템플릿

캔버스의 **실행 환경 → VM 리소스**에서 아래 고정 템플릿을 선택합니다.

| 템플릿 | vCPU | VM RAM | 전체 코드 워커 메모리 상한 | 워커 프로세스 상한 | 디스크 |
| --- | --- | --- | --- | --- | --- |
| 소형 (기본) | 1 | 1 GiB | 768 MiB | 64 | 별도 선택 (기본 6 GiB) |
| 표준 | 2 | 2 GiB | 1536 MiB | 128 | 별도 선택 (기본 6 GiB) |
| 고성능 | 4 | 4 GiB | 3072 MiB | 256 | 별도 선택 (기본 6 GiB) |

워커 상한은 모든 언어가 함께 사용하는 합계입니다. VM 메모리에는 OS와 실행 관리자의
여유 공간이 포함됩니다. VMM의 호스트 MemoryMax는 VM RAM + 512 MiB이고 CPUQuota는
vCPU 수 × 100%입니다. 워커 cgroup에도 선택된 CPU·메모리·프로세스 한도를 적용합니다.
템플릿을 바꾸면 해당 VM을 종료하고 다음 코드 실행 때 새 사양으로 부팅합니다.
모든 언어의 메모리는 초기화되지만 `/workspace` 파일은 보존됩니다. 같은 템플릿을
다시 적용하면 재시작하지 않습니다. 디스크는 CPU·RAM 템플릿과 별도로 6/12/24 GiB 중 선택합니다.

설정은 언어 설정과 같은 revision을 사용하여 캔버스별 서버 파일에 저장됩니다.
`PUT /api/canvases/{id}/runtime/resources`에 `template` (`small`, `medium`, `large`),
`revision`, 필요한 경우 `passwordGrant`를 전송합니다. 설정 읽기 API의 응답에
`resourceTemplate`, `resourceTemplates`가 포함됩니다. 실행 중인 캔버스의 변경은 거부합니다.

호스트는 동시에 최대 4개 VM, VM RAM 합계 8 GiB, vCPU 합계 8개까지 허용합니다.
새 VM을 시작할 때 호스트 MemAvailable에서 새 VM RAM + VMM 512 MiB + 2 GiB 여유를
확보할 수 있는지도 검사합니다. 다른 캔버스가 자원을 사용하는 동안 실행은 거부될 수
있으며 설정만 저장된 비활성 VM에는 자원을 예약하지 않습니다. 정책은 root 소유
`runtime/manager.py`의 상수로 관리합니다.

부팅 인자는 `/var/lib/agora-runtime/launch/<canvasId>.env`에 root 전용으로 생성합니다.
템플릿 이름은 게스트 커널 명령행으로 전달하므로 코드 객체가 리소스 제한을 높일 수
없습니다. 사용자에게 자유 입력하는 메모리 값이나 하이퍼바이저 옵션을 제공하지 않습니다.
VM 설정의 기본값과 템플릿은 공유 모듈 `runtime/runtime_options.py`를 사용합니다.

```bash
python3 -m unittest discover -s runtime/tests -p 'test_*.py'
sudo python3 runtime/tests/resources_integration.py
```

실제 KVM 테스트는 예약된 ID 2147483006을 사용하고 세 템플릿의 vCPU, RAM,
게스트 cgroup과 호스트 MemoryMax, 파일 보존, 메모리 초기화, 동일 설정의 무동작,
잘못된 설정·오래된 revision 거부를 검증한 뒤 테스트 VM과 설정을 제거합니다.


## 디스크 용량 확장

**실행 환경 → VM 디스크**에서 6/12/24 GiB를 선택합니다. OS·언어 런타임·패키지를
포함한 전체 파일시스템 용량이며 `/workspace`만의 할당량은 아닙니다. 기본값은 6 GiB,
축소는 지원하지 않습니다. 용량은 같은 캔버스 참가자가 공유하고 서버 설정에 저장됩니다.

API는 `PUT /api/canvases/{id}/runtime/disk`, 본문은 `sizeGiB`, `revision`, 필요하면
`passwordGrant`입니다. 설정 읽기 응답에 `diskGiB`, `diskSizesGiB`가 포함됩니다.
실행 중·오래된 revision·허용되지 않은 용량·축소 요청은 거부합니다. 동일 용량은
아무 작업도 하지 않습니다. 아직 디스크가 없는 캔버스는 설정만 저장하고 최초 실행 때
선택한 크기를 생성합니다. 이때까지 저장 공간을 예약하지 않습니다.

기존 VM의 확장은 VM을 정지하고 별도의 `guest.growing`에 원본을 복사한 뒤 수행합니다.
복사 중에는 빈 영역의 쓰기를 생략할 수 있지만 `posix_fallocate`로 전체 용량을 실제
예약합니다. e2fsck와 resize2fs로 ext4를 확장하고 파일을 fsync한 뒤 원자적으로
`guest.raw`와 교체합니다. 실패하면 복사본만 지우고 원본을 유지합니다. 복사본의
전체 목표 용량과 서버 여유 8 GiB가 추가로 필요합니다. 공간 부족은 VM 정지 전에
검사합니다. 확대 후 모든 언어의 메모리는 초기화되며 저장 파일은 보존됩니다.

커밋 후 설정 기록 전에 중단된 경우 실제 디스크 파일 크기도 읽어 축소를 방지합니다.
호스트의 RLIMIT_FSIZE는 최대 옵션인 24 GiB를 허용하며 개별 이미지의 실제 크기는
선택값으로 제한됩니다. 이후 `runtime/upgrade-guest`는 기존 확장 용량을 유지한 채
새 OS 이미지의 파일시스템을 늘리고 작업 파일을 복원합니다.

```bash
sudo python3 runtime/tests/disk_integration.py
```

예약된 ID 2147483007에서 실제 6→12→24 GiB 확장, ext4 용량, 호스트 공간 예약,
파일 체크섬 보존, 메모리 초기화, 축소·잘못된 용량·오래된 설정 거부와 재시작 후
유지를 검증하고 테스트 VM을 정리합니다.

## 삭제된 캔버스의 실행 환경 정리

Spring이 캔버스 삭제를 SQL에 커밋한 뒤 전용 Unix socket으로
`{"canvasId":123,"action":"delete"}`를 보냅니다. 웹 클라이언트에 직접 노출하는
실행 환경 삭제 API는 없습니다. 삭제는 코드 실행·설정 변경과 같은 캔버스 잠금으로
직렬화하며 실행 중이면 거부하여 Spring의 영속 정리 큐가 나중에 재시도하게 합니다.

실행 관리자는 `/var/lib/agora-runtime/deleted/<id>` 표식을 먼저 디스크에 기록합니다.
이후 VM과 전용 네트워크 서비스를 종료하고 VM 디렉터리, 설정과 임시 설정,
launch 환경 파일, systemd 리소스 override, `backup-*/<id>.raw`를 제거합니다.
다른 VM의 디스크와 공용 OS 백업은 건드리지 않습니다. 삭제는 멱등적입니다.
뒤늦게 도착한 실행 요청은 표식으로 차단하므로 삭제된 VM이 다시 생성되지 않습니다.
작업 완료 응답은 디렉터리 변경을 fsync한 뒤 보냅니다. 작은 삭제 표식은 재생성을
막기 위해 유지하며 DB를 초기화해 같은 ID를 재사용할 때 운영자 검토가 필요합니다.

```bash
python3 -m unittest discover -s runtime/tests -p 'test_*.py'
sudo python3 runtime/tests/cleanup_integration.py
```

실제 KVM 검증은 예약 ID 2147483008만 사용합니다. 실행 중인 VM의 종료, 작업 파일과
디스크·설정·네트워크·리소스 override·해당 백업 제거, 반복 삭제, 재생성 방지 및
다른 백업 보존을 확인한 뒤 테스트 표식과 파일을 정리합니다.


## 코드 실행의 파일 크기

모든 언어 워커와 자식 프로세스의 RLIMIT_FSIZE는 soft/hard 모두 unlimited입니다.
이전의 파일당 4 MiB 제한은 제거했습니다. 파일은 선택된 VM 디스크의 남은 공간까지
쓸 수 있습니다. VM 전체 디스크 용량(6/12/24 GiB), CPU·메모리·프로세스·코드 실행
시간·출력 제한은 별도로 적용됩니다. 호스트의 VMM 디스크 이미지 파일 상한은 게스트
코드의 파일 크기 제한과 별도입니다.

`sudo python3 runtime/tests/file_size_integration.py`로 실제 VM에서 Python의 soft/hard
무제한과 Python·Node.js·Shell·C++·Java의 8 MiB 파일 생성·크기를 검증합니다.
예약 ID 2147483009의 테스트 VM과 파일만 사용하고 정리합니다.

## Java Maven 의존성

Java 실행 설정의 `dependencies` 배열에 Maven Central 좌표 `group:artifact:version`을
지정합니다. 고정 릴리스만 허용하며 최대 20개, 중복 group/artifact와 임의 URL은
거부합니다. 기존 설정 파일은 빈 배열을 기본값으로 사용합니다.

`POST /api/canvases/{id}/runtime/java/dependencies`는 캔버스 접근 권한을 확인하고
서버에 저장된 Java 설정을 사용합니다. UI의 **이 언어에 적용**은 저장 후 이 작업을
자동 수행합니다. 실행 관리자는 VM의 UID 1000, 기존 job cgroup 안에서 Maven
`maven-dependency-plugin:3.11.0:build-classpath`를 실행해 하위 의존성과 runtime
클래스패스를 준비합니다. POM은 허용된 좌표로 생성하고 중앙 저장소를 HTTPS로 고정합니다.
플러그인은 게스트 이미지에 미리 캐시하며 애플리케이션 JAR은 VM NAT로 내려받습니다.
호스트에서 Maven이나 사용자 라이브러리를 실행하지 않습니다.

Maven 캐시는 `/workspace/.m2/repository`, 설정별 클래스패스는
`/workspace/.java-dependencies/<engine-and-dependencies-hash>/classpath.txt`에 저장됩니다.
설치 성공 후에만 클래스패스 파일을 교체합니다. JShell은 시작할 때 이 경로를
`addToClasspath`로 등록해 코드 객체의 컴파일과 실행에 함께 사용합니다.
JDK 17/21/25에서 외부 라이브러리와 하위 의존성 호출을 검증했습니다.
재시작 시 캐시를 재사용하며 JDK가 바뀌면 새 클래스패스를 준비합니다.

설치 및 재시도 시 Java 워커만 중지하여 이전 클래스가 남지 않게 합니다. 다른 언어의
메모리와 작업 파일은 유지합니다. 설치는 별도의 작업이며 Maven 160초/게스트 175초,
호스트 응답 185초/Spring 220초/해당 gateway 경로 240초 한도를 사용합니다.
일반 코드 실행의 15초 한도는 유지합니다. 실패는 `installed:false`와 진단 출력으로
반환하고 다시 시도할 수 있습니다. 설치 전 Java 코드는 명확한 안내와 종료 코드 1을
반환합니다. Maven 실행도 VM CPU·메모리·프로세스·디스크 한도를 적용받습니다.

```bash
sudo python3 runtime/tests/java_dependencies_integration.py
sudo python3 runtime/tests/numpy_integration.py
```

첫 테스트는 예약 ID 2147483011에서 Commons Lang/Jackson 및 하위 JAR import,
코드 객체 간 상태, 다른 언어 상태 보존, 모든 제공 JDK, 캐시 및 VM 재시작을 검증합니다.
두 번째는 ID 2147483010에서 실제 pip install NumPy와 배열/선형방정식/내적 계산,
재시작 후 재사용을 검증합니다. 두 테스트 모두 자신이 만든 VM과 패키지만 정리합니다.


## Python 3.14 및 3.15

CPython 3.14/3.15 공식 slim-trixie 이미지를 게스트에 포함합니다.
실행 환경의 Python 인터프리터에서 선택하며 기존 기본값은 3.12입니다.
인터프리터는 `/opt/python/3.14/bin/python3.14`, `/opt/python/3.15/bin/python3.15`,
공유 라이브러리는 각 prefix의 lib, 패키지는 `/workspace/.python/3.14`,
`/workspace/.python/3.15`로 분리합니다. 최적화·바이트코드·경고 설정과 코드 객체 사이의
변수·함수 공유를 동일하게 지원합니다.

Shell의 python/python3/pip/pip3는 선택한 Python 버전을 따르며 버전별 패키지 경로도 자동 설정됩니다.
따라서 보통 `pip install numpy`만 실행하면 됩니다. 아래처럼 버전을 명시하는 설치도 가능합니다:

```bash
LD_LIBRARY_PATH=/opt/python/3.15/lib \
PYTHONPATH=/workspace/.python/3.15 \
PIP_TARGET=/workspace/.python/3.15 \
/opt/python/3.15/bin/python3.15 -m pip install --only-binary=:all: numpy
```

3.14는 위 경로의 3.15를 3.14로 바꿉니다. 설치한 뒤 Python 실행 환경에서 같은 버전을
선택하고 import합니다. 파일과 패키지는 VM 재시작 후 유지됩니다.

```bash
sudo python3 runtime/tests/new_python_integration.py
```

예약 ID 2147483012에서 두 버전의 실제 인터프리터·SSL/SQLite/zstd·최적화·공유 상태,
버전별 pip NumPy 설치와 계산·패키지 격리·VM 재시작 후 재사용을 검증한 뒤 정리합니다.

배포 검증에서 Python 3.14.10 및 3.15.2 각각에 pip로 NumPy 2.5.4를 설치하고
내적 결과 32, 버전별 패키지 경로, 다른 버전과의 격리 및 VM 재시작 후 재사용을
확인했습니다. 운영 HTTPS 설정 API에서도 두 버전 선택과 실제 실행을 검증했습니다.


## pip와 선택된 Python 워커의 일치

Shell의 `python`, `python3`, `pip`, `pip3` 명령은 캔버스에서 선택한 Python을 사용합니다.
버전을 바꿔도 기존 Shell 프로세스와 변수는 유지하고 다음 요청의 Python 관련 환경만
갱신합니다. 사용자 추가 PATH/PYTHONPATH/LD_LIBRARY_PATH는 유지하면서 다른 Python의
관리 패키지 경로를 제거합니다. Python 코드에서 실행하는 subprocess의 pip도 같은
버전으로 연결합니다. `python3.12`, `pip3.15` 같은 명시적 버전 명령은 해당 버전과
패키지 경로를 선택합니다. 런타임의 실제 바이너리를 호출하는 고정 shim을 사용하므로
복사된 Python 이미지의 pip shebang이 잘못된 인터프리터를 가리키지 않습니다.

호스트 실행 관리자는 Shell 요청에 서버의 `pythonRuntime` 설정을 함께 전달합니다.
공개 실행 API는 변경되지 않습니다. 실행 중인 Python 워커는 매 셀 전에 import cache를
갱신하므로 나중에 pip로 설치된 NumPy도 초기화 없이 import할 수 있습니다.

```bash
sudo python3 runtime/tests/pip_alignment_integration.py
```

예약 ID 2147483013에서 먼저 numpy가 없는 Python 3.15 워커를 실행하고 plain pip로
설치한 뒤 같은 워커에서 import/계산·기존 변수 보존을 검증합니다. 이어 Python 3.14로
변경한 기존 Shell의 PID/변수/사용자 검색 경로 보존, plain pip3 설치·nested pip,
명시적 버전 명령과 VM 재시작 후 재사용을 확인하고 테스트 VM만 삭제합니다.
