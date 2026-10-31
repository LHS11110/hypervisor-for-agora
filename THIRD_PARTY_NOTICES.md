# Third-party notices and binary distribution review

This repository's own scripts/workers are Apache-2.0 licensed. They download/install separate third-party binaries; the project LICENSE does not relicense those binaries.

| Component | Review basis |
| --- | --- |
| Cloud Hypervisor v54.0 and ch-remote | Upstream Apache-2.0/BSD-3-Clause terms; preserve the selected license and upstream notices. Binary Rust dependencies may add their own notices. |
| edk2 CLOUDHV firmware ch-97eeb7b09 | edk2 BSD-2-Clause-Patent and component-specific terms; inspect that firmware build's complete license inventory. |
| Ubuntu 24.04 guest image/kernel | Ubuntu contains many licenses; Linux GPL-2.0 and other component obligations apply when conveying images. A download checksum/GPG signature is not a source/license bundle. |
| Clang/LLVM runtime and JIT worker libraries | Apache-2.0 WITH LLVM-exception and each linked library's terms. |
| GCC/OpenJDK, Python, Node, Bash and installed packages | Each runtime/library retains its own license, including relevant GPL/classpath/runtime exceptions. Keep guest package copyright files. |
| Maven/pip/npm packages installed by users | Per-package licenses vary and are not covered by this repository's license. |

Downloaded firmware, guest disks, caches, binaries and snapshots are excluded by `.gitignore`. The Git source does not ship them. Before distributing a VM image, executable or appliance, create an exact package/binary inventory, preserve LICENSE/NOTICE files and provide corresponding source or a valid source offer where required. This audit does not establish compliance of a future combined VM image.

Sources: [Cloud Hypervisor contribution/license policy](https://github.com/cloud-hypervisor/cloud-hypervisor/blob/main/CONTRIBUTING.md), [edk2 license](https://github.com/tianocore/edk2/blob/master/License.txt), [GNU license FAQ](https://www.gnu.org/licenses/gpl-faq.html).
