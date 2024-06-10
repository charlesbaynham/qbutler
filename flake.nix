{
  description = "Manage a complex research experiment with lots of moving parts and drifting calibrations automatically and repeatably.";

  inputs = {
    poetry2nix = {
      url = "github:nix-community/poetry2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, poetry2nix }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        # see https://github.com/nix-community/poetry2nix/tree/master#api for more functions and examples.
        pkgs = nixpkgs.legacyPackages.${system};
        inherit (poetry2nix.lib.mkPoetry2Nix { inherit pkgs; }) mkPoetryPackages mkPoetryEnv;
      in
      {
        packages = {
          qbutler = mkPoetryPackages { projectDir = self; };
          default = self.packages.${system}.qbutler;
        };

        # Shell for app dependencies.
        #
        #     nix develop
        #
        # Use this shell for developing your app.
        devShells.default = mkPoetryEnv {
          projectDir = ./.;
        };

        # Shell for poetry.
        #
        #     nix develop .#poetry
        #
        # Use this shell for changes to pyproject.toml and poetry.lock.
        devShells.poetry = pkgs.mkShell {
          packages = [ pkgs.poetry ];
        };
      });
}
