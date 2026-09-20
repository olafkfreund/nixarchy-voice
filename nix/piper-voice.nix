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

  # Every voice carries its own licence and its own model card, because they
  # are not the same licence: the weights are published MIT, the corpora they
  # were trained on are not, and one of them is research-only (see lessac).
  # `attribution` is the credit the dataset's terms require in any interface
  # that generates speech -- null when none is required.
  mkVoice =
    { name, path, onnxHash, jsonHash, modelCardHash, description, license, attribution ? null, dataset }:
    runCommand "piper-voice-${name}"
      {
        meta = {
          inherit description license;
          homepage = "https://huggingface.co/rhasspy/piper-voices";
        };
        passthru = { voiceName = name; inherit attribution dataset; };
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
      # Shipped, not just read at build time: the terms live with the files.
      cp ${fetchurl {
        url = "${base}/${path}/MODEL_CARD";
        sha256 = modelCardHash;
      }} $out/MODEL_CARD
    '';
in
lib.makeExtensible (self: {
  inherit mkVoice;

  "en_GB-jenny_dioco-medium" = mkVoice {
    name = "en_GB-jenny_dioco-medium";
    path = "en/en_GB/jenny_dioco/medium";
    onnxHash = "00bbaa0wxmz5ni3ix716l2867f2avsmz8sx6jb9rs4wy406n7726";
    jsonHash = "1m2dlp5mijqg3csa9a48c700kvzrgl2yndryasv3r6kw64xak9x9";
    modelCardHash = "0i8dw8303q2z2h8n126pvn9hfjq0rw1f1dha4r34zcba34rfr2mw";
    description = "Piper voice: British female, warm and conversational";
    license = lib.licenses.mit;
    # The Jenny dataset is Dioco's, and its terms ask for the credit to be
    # shown wherever the voice speaks. That is why the string travels with the
    # derivation instead of being typed into the README once.
    attribution = "Jenny (Dioco)";
    dataset = "https://github.com/dioco-group/jenny-tts-dataset";
  };

  "en_GB-cori-high" = mkVoice {
    name = "en_GB-cori-high";
    path = "en/en_GB/cori/high";
    onnxHash = "00sr1brqi3yxlq2ndvzf5137g47w7py6yqnpa148m3y96kb4s2s7";
    jsonHash = "1v5dwbqxdnlrwh4vbg1j8qd06yz839nf9jw17hpw44hncysvazwy";
    modelCardHash = "13r0b2m69wk0a6py1sjwfysp6mvy559h46phbm55phxnd38pnvhk";
    description = "Piper voice: British female, measured, highest quality tier";
    license = lib.licenses.mit;
    # LibriVox, public domain. No credit required, so none is claimed.
    attribution = null;
    dataset = "https://librivox.org";
  };

  "en_US-lessac-high" = mkVoice {
    name = "en_US-lessac-high";
    path = "en/en_US/lessac/high";
    onnxHash = "02cyrp5xsr5pr4y892i270zzxm1j4191c5aaycvp209qlv1zgasc";
    jsonHash = "0bs1j8d97v6bsvfp82h50a23kckz1scfvf312ny5gwjrk1yvjhnv";
    modelCardHash = "003iham233rlf6nrd1im01mragnrj3c0pbvnvl8zq3vsjinq4wbn";
    description = "Piper voice: American female, crisp diction, highest quality tier";
    # The WEIGHTS are published MIT with the rest of piper-voices. The DATA
    # they were trained on is not: the card points at Blizzard 2013 (Lessac
    # Technologies / Voice Factory), whose licence is a per-licensee research
    # agreement -- "Research Purposes" there explicitly excludes commercial use
    # and the licensing of voice synthesis products. We ship the weights, not
    # the corpus, and the two are not the same artefact; whether that makes
    # shipping this voice on by default acceptable is a question for a human,
    # and it is asked on issue #18 rather than answered here.
    #   card:    ${base}/en/en_US/lessac/high/MODEL_CARD
    #   dataset: https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html
    license = lib.licenses.mit;
    attribution = null;
    dataset = "https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/";
  };

  default = self."en_GB-jenny_dioco-medium";
})
