# 보안 분석 및 적용 상태

분석일: 2026-10-24. 대상은 실행·설치·이미지 준비 코드, 이 호스트의 실제 KVM 실행,
Cloud Hypervisor v54.0의 공개 보안 공지입니다. VMM/커널 소스 전체 감사는 수행하지 않았습니다.

## 기본 실행의 보안 경계

`hypervisor run`은 root로 관리하는 `agora-hypervisor.service`만 시작합니다.
VMM 자체는 로그인 불가 **agora-vmm** 계정으로 실행합니다. 관리 권한은 root에 한정하며
작업 계정에는 sudoers/Polkit 예외를 추가하지 않았습니다.

| 위협 | 적용한 보호 |
| --- | --- |
| VMM syscall 악용 | upstream 스레드별 `--seccomp true`, systemd 위험 syscall 차단, native ABI 고정 |
| 권한 상승 | non-root UID, 모든 capability 집합 제거, NoNewPrivileges, SUID/SGID·namespace 생성 금지 |
| 같은 작업 UID와 권한 공유 | 작업 계정과 별도 전용 시스템 계정, 전용 PID namespace와 제한된 proc |
| 호스트 파일·소켓 유출 | 최소 root 파일시스템과 별도 mount namespace, root와 실행 파일 읽기 전용, Landlock 강제 |
| abstract Unix socket·호스트망 접근 | 별도 network namespace, AF_UNIX만 허용, IPAddressDeny=any, TAP 없음 |
| API 탈취 | systemd가 root:root 0600 소켓을 만들고 fd 3으로 전달; 호스트 소켓 경로는 VMM root에 노출하지 않음 |
| 호스트 장치 공격 | private /dev, 장치 cgroup 정책, KVM만 추가 허용; VFIO·vsock·공유 파일시스템 없음 |
| CPU·메모리·fork 고갈 | cgroup CPUQuota=200%, MemoryMax=12G, MemorySwapMax=0, TasksMax=64, OOMPolicy=stop |
| 디스크 I/O 고갈 | backing filesystem 장치에 읽기·쓰기 각각 50 MiB/s, 1,000 IOPS 제한 |
| 디스크 고갈 | import 최대 raw 10 GiB/seed 16 MiB, 파일 공간 선할당, VMM 파일 크기 상한 10 GiB, Landlock으로 파일 생성 제한 |
| 로그 고갈·터미널 escape | serial null, 출력은 전용 휘발성 journal; 약 16 MiB 보관 한도, 메시지 rate limit, 외부 전달 없음 |
| 메모리·환경 정보 유출 | core dump 0, 호스트 작업 환경 미상속, ProtectHome/ProtectKernelLogs, 실행 가능 메모리 쓰기 금지 |
| 이미지 형식 악용 | `image_type=raw,backing_files=off`, seed 읽기 전용 |
| 다운로드·설치 변조 | VMM/펌웨어 SHA256 pin, 검증한 바이트 스냅샷 설치, 이미지 날짜/서명자/hash pin |
| 입력 경로 바꿔치기 | 관리 import/install은 O_NOFOLLOW와 directory fd로 읽고 fstat 소유자·형식·hardlink·쓰기 권한 검사 |
| 경쟁 실행·종료 후 잔존 | 관리 명령 flock, 단일 systemd 서비스, KillMode=control-group, 15초 후 잔존 프로세스 강제 종료 |

root 파일시스템에는 게스트 디스크와 seed, 검증된 VMM/펌웨어, 커널 가상 파일시스템과
필요한 journal endpoint만 노출합니다. 호스트 home, D-Bus, 사용자 runtime/SSH agent는
노출하지 않습니다. `/dev/kvm`은 VM 실행에 필요한 신뢰 경계로 남습니다.
Landlock v54.0의 ABI v3만으로 AF_UNIX를 격리할 수 없으므로 mount/network namespace가
pathname/abstract Unix 소켓 노출을 추가로 차단합니다.

메모리/CPU/pids 한도는 VMM 프로세스 전체에 적용하며 게스트 RAM 값과 별개입니다.
디스크 import는 공간이 부족하거나 선할당을 지원하지 않으면 실패합니다. 실패한 `.part`는
검사 후 관리자가 제거해야 하며 실행 가능한 완성 디스크로 취급하지 않습니다.
기본 경로는 신규 파일 생성을 Landlock으로 제한하고 직렬 로그를 생성하지 않습니다.
상한이 설정돼 있어도 다른 호스트 작업을 위한 여유 공간은 별도로 확보해야 합니다.

전용 journal은 `RuntimeMaxUse=16M`, `RuntimeMaxFileSize=1M`, 보관 기간 7일을 적용합니다.
journal 순환/metadata 때문에 수학적으로 정확한 16 MiB quota는 아닙니다.
호스트 systemd의 서비스 시작/종료 메시지는 호스트 journal 정책을 따릅니다.

## 공급망 검증

[Canonical 공식 절차](https://ubuntu.com/docs/public-images/public-images-how-to/verify-image-checksum/)에 따라
배포판 패키지의 `/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg`로 GPG 서명을 검증합니다.
`config/ubuntu-image.json`에 release-20261017, signer fingerprint, 이미지 SHA256을 고정합니다.
서명자 또는 digest가 다르거나 manifest/image가 변조되면 변환 전에 중단합니다.
이미지 다운로드는 최대 1 GiB, manifest/signature는 각 1 MiB로 제한합니다.
HTTPS 이외의 전송/리다이렉트를 거부하고 사용자 `.curlrc`를 읽지 않습니다.

VMM/펌웨어는 공식 GitHub 릴리스 API digest와 대조한 값을 `config/SHA256SUMS`에 고정했습니다.
이 아티팩트에는 별도의 발행자 서명 검증을 구성하지 않았으므로 upstream 릴리스와 최초 pin의
신뢰는 남습니다. root 소유로 설치하여 작업 계정이나 VMM이 바이너리를 수정할 수 없게 했습니다.
릴리스·이미지를 갱신할 때는 공식 공지와 서명/hash를 재검토하고 테스트해야 합니다.

## 확인한 공개 취약점

- [GHSA-jmr4-g2hv-mjj6 / CVE-2026-27211](https://github.com/cloud-hypervisor/cloud-hypervisor/security/advisories/GHSA-jmr4-g2hv-mjj6): QCOW backing-file 기반 호스트 파일 유출. 영향 v34.0–v50.0, 수정 v51.0/v50.1. v54.0은 수정 버전 이후이며 raw 지정과 Landlock을 추가 적용했습니다.
- [GHSA-f47p-p25q-83rh / CVE-2026-45782](https://github.com/cloud-hypervisor/cloud-hypervisor/security/advisories/GHSA-f47p-p25q-83rh): virtio-block async I/O use-after-free. 영향 v21.0–v51.1, 수정 v52.0/v51.2. v54.0은 수정 버전 이후입니다.

## 검증 및 호스트 변경

이 호스트에 root 소유 service/socket unit, 최소 root, 전용 nologin 계정, journald 설정을
설치했습니다. 자동 시작은 활성화하지 않았습니다. 준비된 Ubuntu 게스트가 없으므로
운영 디스크 import와 상시 VM 실행은 하지 않았습니다. 테스트 VM과 임시 서비스는 제거했습니다.

- `tests/test_managed_security.py`: 설치된 바이너리와 같은 service 정책으로 실제 KVM/UEFI 부팅;
  전용 UID·mount/pid/net/ipc namespace, 모든 스레드의 seccomp/NNP/빈 capability 집합,
  호스트 파일/socket 부재, 작업 계정의 API 연결 거부, 실제 cgroup CPU/memory/swap/pids/io 값,
  root/실행 파일 ro mount, core/file-size limit, 서비스 종료와 socket 제거 확인.
- `tests/test_image_verification.py`: 실제 Canonical 서명 fixture 검증, manifest/image 변조,
  예상과 다른 signer/digest 거부 확인.
- `tests/test_security.py`: 개발 경로의 KVM/UEFI, seccomp/Landlock, 파일 변조·symlink/hardlink·옵션 삽입,
  중복 실행, 종료 signal 정리 확인.

전체 명령은 README에 있습니다. root로 실행하는 개발 테스트와 일반 사용자로 실행하는
서비스 테스트는 각각 skip하므로 skip 여부를 확인하세요. Ubuntu OS 부팅·SSH·네트워크는
검증 범위에 포함되지 않습니다. 서비스는 의도적으로 오프라인입니다.

개발 명령 `run-dev`는 기존 개인 UID 경로이며 전용 namespace/cgroup/journal 격리를
제공하지 않습니다. 신뢰하지 않는 게스트에는 사용하지 않습니다. 기본 서비스 실행에
실패해도 개발 모드로 자동 전환하지 않습니다.

## 남는 신뢰 경계

호스트 root, 커널/KVM, CPU/펌웨어, systemd, upstream 공급자 및 검토한 저장소 코드는
신뢰해야 합니다. seccomp가 허용하는 syscall·KVM ioctl의 미발견 취약점이나 하드웨어
부채널까지 제거한 것은 아닙니다. 호스트 커널/microcode, VMM 및 게스트의 보안 업데이트는
지속적으로 필요합니다. 이번 작업에서 호스트 전체 업데이트나 재부팅은 수행하지 않았습니다.
네트워크가 필요하면 격리된 전용망과 egress/메타데이터 차단 정책을 새로 검증해야 하며,
호스트 TAP를 기본 서비스에 그대로 연결하지 않습니다.

참고: [upstream 위협 모델](https://github.com/cloud-hypervisor/cloud-hypervisor/blob/v54.0/docs/threat-model.md),
[Landlock](https://github.com/cloud-hypervisor/cloud-hypervisor/blob/v54.0/docs/landlock.md),
[systemd 격리 설정](https://www.freedesktop.org/software/systemd/man/260/systemd.exec.html).


## 캔버스 코드 실행 VM의 NAT 정책

위의 오프라인 설명은 기본 단일 VM 서비스에 해당합니다. 별도 `agora-runtime@`는
공용 IPv4 아웃바운드가 허용됩니다. VM마다 root 소유 네트워크 서비스가 전용
네임스페이스·TAP·veth를 만들고, 네임스페이스와 호스트 양쪽의 conntrack 방화벽을
통과한 통신만 NAT 처리합니다. 반환 패킷은 ESTABLISHED/RELATED만 허용하며 DNAT나
인바운드 허용 규칙은 없습니다. VM에서 호스트·사설 대역·링크 로컬 메타데이터·다른
VM에 접근하는 경로는 차단합니다. 호스트 공인 IP도 추가 차단 목록에 지정합니다.
IPv6는 비활성화했습니다. 허용된 아웃바운드 연결 위에서 주고받는 데이터는 이 정책의
허용 범위이며, 애플리케이션 수준의 데이터 흐름을 검사하는 프록시는 아닙니다.

네트워크 설정용 root oneshot만 NET_ADMIN/SYS_ADMIN을 사용합니다. VMM은 기존 전용
사용자·빈 capability 집합·seccomp·Landlock을 유지하며 자기 TAP와 해당 tun_flags
파일의 읽기 권한만 추가합니다. 사용자 코드는 게스트 UID 1000으로 실행됩니다.
파이썬 패키지는 쓰기 가능한 `/workspace/.python`에 설치됩니다.

`runtime/tests/network_integration.py`는 실제 VM에서 공용 DNS/TLS와 pip 설치,
재시작 후 설치 파일 유지, 내부 목적지 차단과 실제 리스너에 대한 신규 인바운드
차단을 검증합니다. 기존 SSH·웹 서버·Docker 포트 노출 규칙은 변경하지 않습니다.
