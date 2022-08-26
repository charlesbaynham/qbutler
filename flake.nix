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

    
  };

  outputs = { self, nixpkgs, flake-utils, mach-nix }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        
      in
      rec {
        
        devShell = builtins.trace "Hello" pkgs.mkShell {
          name = "qbutler-devShell";
          buildInputs = [];
        };

      }
    );
}
