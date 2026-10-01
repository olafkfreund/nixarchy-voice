# A whisper.cpp model, for the two things on this machine that listen without
# the API: local dictation (`omarchy-voice ask`) and the wake word.
#
# whisper-cpp ships `whisper-cpp-download-ggml-model`, which fetches at run
# time into the user's home. That is the wrong shape here twice over: a Nix
# package should not need the network the first time it is used, and a wake
# word that silently does nothing until someone runs a download script is a
# feature that looks broken rather than one that is off.
#
# `.en` models where one exists. They are smaller and more accurate than the
# multilingual ones at the same size, and both callers are English-only -- the
# wake word is a single English word and the dictation path feeds a planner
# whose persona is written in English. large-v3-turbo has no `.en` build: it is
# multilingual, but the daemon pins English with `-l en`.
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

  "large-v3-turbo-q5_0" = mkModel {
    name = "large-v3-turbo-q5_0";
    sha256 = "1qm7zxamlvac564c3270wqqqks5wc7532q3fqi01zbfmkiq22hir";
    description = "whisper.cpp large-v3-turbo, 5-bit (~574 MB) — for a GPU; ~20 s an utterance on a CPU";
  };

  # turbo is the default because the Vulkan build runs it in about 0.2 s and it
  # hears names base.en misses (#177). On a CPU it takes about 20 s, so the
  # package also ships base.en and the daemon switches to it when whisper finds
  # no GPU.
  default = self."large-v3-turbo-q5_0";
})
