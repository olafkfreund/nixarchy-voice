# A Piper voice: the ONNX model and the JSON that describes it.
#
# Piper cannot speak without one -- `-m MODEL` is required and nothing is
# bundled -- so the package that wraps piper has to carry a voice or the
# spoken feedback path is dead code. It was: feedback.py called
# `piper --output-raw` with no model at all, which cannot ever have run.
#
# The two files must sit in one directory: given only `-m`, piper looks for
# its config beside the model as `<model>.json`.
{ lib, fetchurl, runCommand }:

let
  base = "https://huggingface.co/rhasspy/piper-voices/resolve/main";

  mkVoice = { name, path, onnxHash, jsonHash, description }:
    runCommand "piper-voice-${name}"
      {
        meta = {
          inherit description;
          homepage = "https://huggingface.co/rhasspy/piper-voices";
          # The voices are MIT; the corpora they were trained on are not all
          # the same licence. See the model card for any one of them.
          license = lib.licenses.mit;
        };
        passthru.voiceName = name;
      } ''
      mkdir -p $out
      cp ${fetchurl {
        url = "${base}/${path}/${name}.onnx?download=true";
        sha256 = onnxHash;
      }} $out/${name}.onnx
      cp ${fetchurl {
        url = "${base}/${path}/${name}.onnx.json?download=true";
        sha256 = jsonHash;
      }} $out/${name}.onnx.json
    '';
in
lib.makeExtensible (self: {
  inherit mkVoice;

  "en_GB-jenny_dioco-medium" = mkVoice {
    name = "en_GB-jenny_dioco-medium";
    path = "en/en_GB/jenny_dioco/medium";
    onnxHash = "00bbaa0wxmz5ni3ix716l2867f2avsmz8sx6jb9rs4wy406n7726";
    jsonHash = "1m2dlp5mijqg3csa9a48c700kvzrgl2yndryasv3r6kw64xak9x9";
    description = "Piper voice: British female, warm and conversational";
  };

  "en_GB-cori-high" = mkVoice {
    name = "en_GB-cori-high";
    path = "en/en_GB/cori/high";
    onnxHash = "00sr1brqi3yxlq2ndvzf5137g47w7py6yqnpa148m3y96kb4s2s7";
    jsonHash = "1v5dwbqxdnlrwh4vbg1j8qd06yz839nf9jw17hpw44hncysvazwy";
    description = "Piper voice: British female, measured, highest quality tier";
  };

  "en_US-lessac-high" = mkVoice {
    name = "en_US-lessac-high";
    path = "en/en_US/lessac/high";
    onnxHash = "02cyrp5xsr5pr4y892i270zzxm1j4191c5aaycvp209qlv1zgasc";
    jsonHash = "0bs1j8d97v6bsvfp82h50a23kckz1scfvf312ny5gwjrk1yvjhnv";
    description = "Piper voice: American female, crisp diction, highest quality tier";
  };

  default = self."en_GB-jenny_dioco-medium";
})
