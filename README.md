# colcon-ros-cargo
Build cargo projects with colcon.


## Usage
Packages need to have a `package.xml` in addition to `Cargo.toml`. You should see such packages classified as `ament_cargo` in the output of `colcon list`. If they are classified as `ros.ament_cargo` instead, the `colcon-ros-cargo` extension has not been found by `colcon`. Make sure that you have built and loaded (`source install/setup.bash`) the extension. 

Simply list dependencies (other `ament_cargo` packages or message packages) in `Cargo.toml` and `package.xml` as if they were hosted on crates.io. `colcon-ros-cargo` will find the dependencies and create a `.cargo/config.toml` file that helps cargo find these packages.

Extra arguments to `cargo` can be passed via the `--cargo-args` option, e.g. `colcon build --cargo-args --release`.

After building, run binaries with `ros2 run`.

`colcon-ros-cargo` also aims to support using `cargo` directly as the primary build tool. Just build with `colcon` once, to make sure all non-Cargo dependencies are built and the `.cargo/config.toml` file exists, and then using `cargo` will just work – `cargo build`, `cargo clippy`, `cargo doc`, etc. When the dependency graph changes, rebuild with `colcon`.


## Limitations
This is by far not a perfect build system.

Notably, there is _quadratic_ build cost as a function of the dependency chain length. To illustrate this, assume there are packages A, B and C, where C depends on B and B depends on A. If colcon builds this workspace, it builds A first, then B, then C. However, Cargo will _also_ build all the dependencies, i.e., to build B, Cargo will build A again, and to build C, it will build A and B again.

`colcon test` is not yet supported.