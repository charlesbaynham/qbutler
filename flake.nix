{
  description = "Environment for running qbutler unit tests";

  inputs = {
    artiq.url = "github:dnadlinger/artiq?ref=dpn/emulator";
    src-ndscan = {
      url = "git+https://gitlab.com/aion-physics/code/artiq/forks/ndscan.git";
      flake = false;
    };
    src-oitg = {
      url = "github:OxfordIonTrapGroup/oitg";
      flake = false;
    };
  };

  outputs = { self, artiq, src-ndscan, src-oitg }:
    let
      nixpkgs = artiq.nixpkgs;
      artiqpkg = artiq.packages.x86_64-linux.artiq;
      libartiq-emulator = artiq.packages.x86_64-linux.libartiq-emulator;

      oitg = nixpkgs.python3Packages.buildPythonPackage {
        name = "oitg";
        src = src-oitg;
        format = "pyproject";
        propagatedBuildInputs = with nixpkgs.python3Packages; [
          h5py
          scipy
          statsmodels
          poetry-core
          poetry-dynamic-versioning
        ];
        installCheckPhase = ''
          ${nixpkgs.python3.interpreter} -m unittest discover test
        '';
      };

      ndscan = nixpkgs.python3Packages.buildPythonPackage {
        name = "ndscan";
        src = src-ndscan;
        format = "pyproject";
        nativeBuildInputs = with nixpkgs.python3Packages; [
          hatchling
        ];
        propagatedBuildInputs = with nixpkgs.python3Packages; [
          artiqpkg
          h5py
          numpy
          oitg
        ];
        doCheck = false;
        dontWrapQtApps = true;
      };

      qbutler-test-deps = with nixpkgs.python3Packages; [
        pytest
        numpy
        networkx
        matplotlib
      ];
    in {
      devShells.x86_64-linux.default = nixpkgs.mkShell {
        name = "qbutler-unit-test-shell";
        buildInputs = [ artiqpkg libartiq-emulator ndscan oitg ] ++ qbutler-test-deps;
        shellHook = ''
          export PYTHONPATH="$(pwd):$PYTHONPATH"
          export LIBARTIQ_EMULATOR=${libartiq-emulator}/lib/libartiq_emulator.so
        '';
      };
    };

  nixConfig = {
    extra-trusted-public-keys =
      "nixbld.m-labs.hk-1:5aSRVA5b320xbNvu30tqxVPXpld73bhtOeH6uAjRyHc=";
    extra-substituters = "https://nixbld.m-labs.hk";
  };
}
