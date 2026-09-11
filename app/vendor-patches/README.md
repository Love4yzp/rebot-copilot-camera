# SDK extension distribution

Base: Seeed-Projects/reBotArm_control_py at
`d54040596faa94bdc4f8ad93f3f06b33dfe3a1bf` (the validated RS model).

The gitlink remains on this published upstream commit. `rebot-sdk.patch`
contains the independently packaged SDK extensions: motion profile math,
collision queries, fixed end-effector construction, MuJoCo MIT transport,
read-only MeshCat worker and loopback server, plus wheel asset packaging.
The application imports these from `reBotArm_control_py`, not a parallel
backend implementation. No URDF or calibration values are changed.

Run `python app/prepare_sdk.py` before dependency installation; dev.sh does
this automatically. It accepts the exact base, checks whether the patch is
already applied, and refuses conflicting edits. Whitespace-insensitive patch
matching is needed for upstream's mixed CRLF/LF files. It does not stage,
commit, reset, fetch or publish anything.

SDK edits must also update this patch, including untracked SDK source files.
`tests/test_sdk_distribution.py` reconstructs the patched files from the clean
base in a temporary directory and compares every file to the working SDK.
Keep this test green before sharing the parent repository: dirty submodule
files alone are not distributable through its gitlink.

A maintained remote fork/upstream contribution is a later publishing action,
not performed by this local refactor. Until then this checked-in patch is the
reproducible source of the application-specific SDK extension version.
