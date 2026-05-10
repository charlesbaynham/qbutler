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
      libartiq-emulator = artiq.packages.x86_64-linux.libartiq-emulator;

      # sipyco's tests try to bind to ::1 (IPv6 loopback) which isn't available here.
      # Extract the sipyco-1.6 that artiqpkg actually uses (not the 1.4 in nixpkgs) and disable its tests.
      # overridePythonAttrs (not overrideAttrs) is needed so that requiredPythonModules is recomputed,
      # which is what python3.withPackages uses to collect the transitive dependency closure.
      sipyco = (builtins.head (builtins.filter
        (p: (p.pname or "") == "sipyco")
        (artiq.packages.x86_64-linux.artiq.propagatedBuildInputs)
      )).overridePythonAttrs (_: {
        doCheck = false;
        doInstallCheck = false;
      });

      replaceSipyco = deps:
        builtins.map (p: if (p.pname or "") == "sipyco" then sipyco else p) deps;

      artiq-comtools = (builtins.head (builtins.filter
        (p: (p.pname or "") == "artiq-comtools")
        (artiq.packages.x86_64-linux.artiq.propagatedBuildInputs)
      )).overridePythonAttrs (old: {
        propagatedBuildInputs = replaceSipyco old.propagatedBuildInputs;
        doCheck = false;
        doInstallCheck = false;
      });

      artiqpkg = (artiq.packages.x86_64-linux.artiq).overridePythonAttrs (old: {
        propagatedBuildInputs = builtins.map (p:
          if (p.pname or "") == "sipyco" then sipyco
          else if (p.pname or "") == "artiq-comtools" then artiq-comtools
          else p
        ) old.propagatedBuildInputs;
      });

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
        doCheck = false;
        doInstallCheck = false;
      };

      ndscan = nixpkgs.python3Packages.buildPythonPackage {
        name = "ndscan";
        src = src-ndscan;
        format = "pyproject";
        nativeBuildInputs = with nixpkgs.python3Packages; [ hatchling ];
        propagatedBuildInputs = with nixpkgs.python3Packages; [
          artiqpkg
          h5py
          numpy
          oitg
        ];
        doCheck = false;
        dontWrapQtApps = true;
      };

      pythonEnv = nixpkgs.python3.withPackages (ps: [
        artiqpkg
        ndscan
        oitg
        ps.pytest
        ps.black
        ps.isort
        ps.numpy
        ps.networkx
        ps.matplotlib
      ]);
    in {
      devShells.x86_64-linux.default = nixpkgs.mkShell {
        name = "qbutler-unit-test-shell";
        buildInputs = [ pythonEnv libartiq-emulator nixpkgs.llvm_14 nixpkgs.nixfmt ];
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
