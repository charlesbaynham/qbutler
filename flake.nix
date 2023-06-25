## flake.nix for qbutler
#
# CFAB 2022-05-23
#
# The presence of this file defines this repository as a Nix "flake". This
# allows you to:
#
#   * Define all dependencies declaratively (both python and non-python)
#   * Freeze those dependencies for perfect reproducability
#   * Build outputs (e.g. documentation html files)
#   * Depend on other flakes, without needing a centralised repository
#
# Nix has a reputation for being hard to learn (deservedly), but the benefits
# outweigh the time investment involved. I do recommend you take some time to
# read e.g. https://www.tweag.io/blog/2020-05-25-flakes/ or
# https://serokell.io/blog/practical-nix-flakes.
#
# However, if you don't want to, this repository is set up to benefit from Nix
# reproducability without delving past python features. Just edit
# requirements.in or requirementsDev.in to add python dependancies to your
# package. If you do this, run `nix run .#update_requirements` to re-freeze them
# for other non-Nix users (Nix users do not need this step).


{
  description = "Manage a complex research experiment with lots of moving parts and drifting calibrations automatically and repeatably. ";

  inputs = {
    # For packaging
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-21.11";
    flake-utils.url = "github:numtide/flake-utils";
    mach-nix.url = "mach-nix/3.4.0";

    nixpkgs.follows = "mach-nix/nixpkgs";

    artiq = {
      # Here I pin to a version of ARTIQ before ARTIQ's nixpkgs version was
      # updated. After this point, mach-nix is now incompatible with nixpkgs and
      # therefore ARTIQ. I therefore need to move us to something other than
      # nixpkgs if we want to use the latest versions of ARTIQ... Argh!
      url = "git+https://gitlab.com/aion-physics/code/artiq/forks/artiq_fork.git?rev=6cb12bcf0264f4a691393cc1d93bd30791a329ad";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Oxford's ndscan ARTIQ extension + supporting package
    ndscan = {
      url = "github:OxfordIonTrapGroup/ndscan";
      flake = false;
    };
    oitg = {
      url = "github:OxfordIonTrapGroup/oitg";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, flake-utils, mach-nix, artiq, ndscan, oitg }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        versionNum = (pkgs.lib.trivial.importJSON "${self}/VERSION.json").version;
        fullVersion = "${versionNum}+${self.shortRev or "dirty-${self.lastModifiedDate}"}";

        # ARTIQ has a version number which is not semver compliant, so cannot be
        # parsed by mach-nix. Since we know exactly what version of ARTIQ we want,
        # there's no change of mach-nix accidentally choosing the wrong version.
        # We therefore override the version number to anything we want, as long as
        # it's semver compliant. This won't affect what artiq thinks its version
        # number is: it's only used for the mach-nix selection process.
        patched_artiq = artiq.packages.${system}.artiq // { "version" = "0.0.0"; };

        # Packages built with buildPythonPackage but which are not in nixpkgs already
        nonPyPIPackages = [
          patched_artiq

          artiq.packages.${system}.pythonparser
          artiq.packages.${system}.qasync
          artiq.inputs.sipyco.packages.${system}.sipyco
          artiq.inputs.artiq-comtools.packages.${system}.artiq-comtools
        ];

        # Here we define a function which patches all the ARTIQ packages (or
        # rather, packages built with nixpkgs.buildPythonPackage but which are not
        # in nixpkgs) into nixpkgs. This allows mach-nix to see them so that it
        # can choose to update them if required. If we don't do this, and if these
        # packages share dependencies with others which *are* parsed by mach-nix,
        # we'll end up with collisions in our python environment. Note: we must
        # also explicitly tell mach-nix that these are dependencies, otherwise it
        # also won't work for esoteric reasons. Ask me how I know.
        nonPyPIPackagesByName =
          builtins.listToAttrs (
            map (newpkg: ({ name = newpkg.pname; value = newpkg; })) nonPyPIPackages
          );
        # We also compile a list of their names, for adding into requirements
        nonPyPIRequirements = pkgs.lib.concatStringsSep "\n" (map (p: p.pname) nonPyPIPackages);

        qbutler = mach-nix.lib."${system}".buildPythonPackage {
          requirements = (builtins.readFile "${self}/requirements.in") + "\n" + nonPyPIRequirements;
          packagesExtra = [
            ndscan
            oitg
          ];
          overridesPre = [
            (final: prev: nonPyPIPackagesByName)
          ];
          src = self;
          version = fullVersion;
          PYTHON_VERSION_OVERRIDE = fullVersion;
          providers = {
            # This is a bugfix, because pythonparser IS in PyPI, but not the
            # latest version. We therefore force it to use the nixpkgs
            # version, which we've just created via overridePre. Remove once
            # https://github.com/m-labs/pythonparser/issues/31 is closed.
            pythonparser = "nixpkgs";
          };
        };

        buildDevReqs = [
          (
            mach-nix.lib."${system}".mkPython {
              requirements =
                (builtins.readFile "${self}/requirements.in")
                + "\n"
                + nonPyPIRequirements
                + "\n"
                + (builtins.readFile "${self}/requirementsDev.in");
              packagesExtra = [
                ndscan
                oitg
              ];
              overridesPre = [
                (final: prev: nonPyPIPackagesByName)
              ];
              providers = {
                # This is a bugfix, because pythonparser IS in PyPI, but not the
                # latest version. We therefore force it to use the nixpkgs
                # version, which we've just created via overridePre. Remove once
                # https://github.com/m-labs/pythonparser/issues/31 is closed.
                pythonparser = "nixpkgs";
              };
            }
          )

          # These are needed by ARTIQ but not propegated because that's hard in Nix
          pkgs.llvm_11
          pkgs.lld_11

          # These packages are required for the pipeline:
          pkgs.git # needed for pre-commit
          pkgs.librsvg # needed for latex docs conversion of SVGs

          # And this is a convenience, for easy editing of nix files
          pkgs.nixpkgs-fmt
        ];

      in
      rec {
        packages = rec {
          inherit qbutler;

          docs_html = pkgs.stdenv.mkDerivation {
            pname = "qbutler_docs_html";
            version = fullVersion;
            src = self;
            phases = [ "buildPhase" ];
            buildInputs = buildDevReqs;
            SPHINX_APIDOC_OPTIONS = "members,show-inheritance";
            PYTHON_VERSION_OVERRIDE = fullVersion; # Override for sphinx's versioning
            buildPhase = ''
              cp -r $src/* .
              chmod -R +w .
              sphinx-apidoc -o docs/autogen "qbutler"
              sphinx-build docs html_out -b html
              mv html_out $out
            '';
          };

          docs_latex = pkgs.stdenv.mkDerivation {
            pname = "qbutler_docs_latex";
            version = fullVersion;
            src = self;
            phases = [ "buildPhase" ];
            buildInputs = buildDevReqs;
            SPHINX_APIDOC_OPTIONS = "members,show-inheritance";
            PYTHON_VERSION_OVERRIDE = fullVersion; # Override for sphinx's versioning
            buildPhase = ''
              cp -r $src/* .
              chmod -R +w .
              sphinx-apidoc -o docs/autogen "qbutler"
              sphinx-build docs latex -b latex
              mv latex $out
            '';
          };
        };

        defaultPackage = packages.qbutler;

        devShell = pkgs.mkShell {
          name = "qbutler-devShell";
          buildInputs = buildDevReqs;
        };

        apps.docs =
          let
            script = pkgs.writeShellScriptBin "launch_server" ''
              export PATH=${pkgs.lib.makeBinPath buildDevReqs}:$PATH

              exec sphinx-autobuild docs html_out --pre-build 'sphinx-apidoc -o docs/autogen "qbutler"' --watch qbutler
            '';
          in
          { type = "app"; program = "${script}/bin/launch_server"; };

        apps.update_requirements =
          let
            script = pkgs.writeShellScriptBin "update_requirements" ''
              export PATH=${pkgs.lib.makeBinPath buildDevReqs}:$PATH

              pip-compile requirements.in
              pip-compile requirementsDev.in
            '';
          in
          { type = "app"; program = "${script}/bin/update_requirements"; };

        apps.pytest =
          let
            script = pkgs.writeShellScriptBin "pytest" ''
              export PATH=${pkgs.lib.makeBinPath buildDevReqs}:$PATH

              coverage run --omit "tests/*,*/_version.py,/nix/store/*" -m pytest --junitxml=report.xml $1
              test_exit_code=$?
              coverage report
              exit "$test_exit_code"
            '';
          in
          { type = "app"; program = "${script}/bin/pytest"; };
      }
    );
}
