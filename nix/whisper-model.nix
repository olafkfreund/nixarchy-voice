# A whisper.cpp model, for the two things on this machine that listen without
# the API: local dictation (`omarchy-voice ask`) and the wake word.
#
# whisper-cpp ships `whisper-cpp-download-ggml-model`, which fetches at run
# time into the user's home. That is the wrong shape here twice over: a Nix
# package should not need the network the first time it is used, and a wake
# word that silently does nothing until someone runs a download script is a
# feature that looks broken rather than one that is off.
#
# `.en` models throughout. They are smaller and more accurate than the
# multilingual ones at the same size, and both callers are English-only -- the
# wake word is a single English word and the dictation path feeds a planner
# whose persona is written in English.
{ lib, fetchurl, runCommand }:

let
  base = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main";

  mkModel = { name, sha256, description }:
    runCommand "whisper-model-${name}"
      {
        meta = {
          inherit description;
          homepage = "https://huggingface.co/ggerganov/whisper.cpp";
          license = lib.licenses.mit;
        };
        passthru.modelName = name;
      } ''
      mkdir -p $out
      cp ${fetchurl {
        url = "${base}/ggml-${name}.bin?download=true";
        inherit sha256;
      }} $out/ggml-${name}.bin
    '';
in
lib.makeExtensible (self: {
  inherit mkModel;

  "tiny.en" = mkModel {
    name = "tiny.en";
    sha256 = "07qbja4m5isssw42prv227gbyrf3nsjms6h8rlyrkpbgd3w4q7lj";
    description = "whisper.cpp tiny English model (~75 MB) — fastest, enough for a wake word";
  };

  "base.en" = mkModel {
    name = "base.en";
    sha256 = "00nhqqvgwyl9zgyy7vk9i3n017q2wlncp5p7ymsk0cpkdp47jdx0";
    description = "whisper.cpp base English model (~142 MB) — accurate enough to dictate to";
  };

  "small.en" = mkModel {
    name = "small.en";
    sha256 = "0p8yqkwvpl9lyy43yajk305bps0v5z1qgyg0jwh35j7cb1nqs4y6";
    description = "whisper.cpp small English model (~466 MB) — better, noticeably slower on CPU";
  };

  # base.en is the default: tiny.en hears a wake word fine but mishears enough
  # of a dictated sentence to be annoying, and dictation is the path where a
  # wrong word costs a whole round trip to find out.
  default = self."base.en";
})
