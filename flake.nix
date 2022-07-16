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

  # For packaging
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-21.11";
  inputs.flake-utils.url = "github:numtide/flake-utils";
  inputs.mach-nix.url = "mach-nix/3.4.0";

  inputs.nixpkgs.follows = "mach-nix/nixpkgs";

  outputs = { self, nixpkgs, flake-utils, mach-nix }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        versionNum = (pkgs.lib.trivial.importJSON "${self}/VERSION.json").version;
        fullVersion = "${versionNum}+${self.shortRev or "dirty-${self.lastModifiedDate}"}";

        qbutler = mach-nix.lib."${system}".buildPythonPackage {
          requirements = builtins.readFile ./requirements.in;
          src = self;
          version = fullVersion;
          PYTHON_VERSION_OVERRIDE = fullVersion;
        };

        devReqs = [
          (
            mach-nix.lib."${system}".mkPython {
              requirements = builtins.readFile ./requirementsDev.in;
              packagesExtra = [ qbutler ];
            }
          )

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
            buildInputs = [ devReqs ];
            SPHINX_APIDOC_OPTIONS = "members,show-inheritance";
            GIT_DESCRIBE = fullVersion; # Override for sphinx's versioning
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
            buildInputs = [ devReqs ];
            SPHINX_APIDOC_OPTIONS = "members,show-inheritance";
            GIT_DESCRIBE = fullVersion; # Override for sphinx's versioning
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
          buildInputs = devReqs;
        };

        apps.docs =
          let
            script = pkgs.writeShellScriptBin "launch_server" ''
              export PATH=${pkgs.lib.makeBinPath devReqs}:$PATH

              sphinx-apidoc -o docs/autogen "qbutler"
              exec sphinx-autobuild docs html_out
            '';
          in
          { type = "app"; program = "${script}/bin/launch_server"; };

        apps.update_requirements =
          let
            script = pkgs.writeShellScriptBin "update_requirements" ''
              export PATH=${pkgs.lib.makeBinPath devReqs}:$PATH

              pip-compile requirements.in
              pip-compile requirementsDev.in
            '';
          in
          { type = "app"; program = "${script}/bin/update_requirements"; };

        apps.pytest =
          let
            script = pkgs.writeShellScriptBin "pytest" ''
              export PATH=${pkgs.lib.makeBinPath devReqs}:$PATH

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
