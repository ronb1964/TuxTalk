"""The Hugging Face repositories TuxTalk downloads models from, each held to
one commit, and the SHA256 of the model files in them.

Without a commit, a download takes whatever the repository holds that day:
it would depend on someone else's repository staying as it is, the reason
Parakeet moved to TuxTalk's own release (parakeet_ultra.py). huggingface_hub
checks only a file's size, so the weights the engines parse are checked
against their SHA256 here as they arrive; the small text files beside them
are held by the commit alone.

Hugging Face keeps each commit's files in a folder named after it and finds
them there without the network, so a model already downloaded is found as
long as it was downloaded at that commit. Each of these repositories last
changed in 2023 or 2024, before TuxTalk downloaded from it, so every cache
holds exactly these. A model updated upstream needs a new commit and new
hashes here, never a changed entry.

Only the repositories TuxTalk still downloads from are here. Earlier
versions' downloads that are only looked for (the original Parakeet's) keep
whatever revision the cache has.
"""

REVISIONS = {
    "Systran/faster-whisper-tiny": "d90ca5fe260221311c53c58e660288d3deb8d356",
    "Systran/faster-whisper-base": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
    "Systran/faster-whisper-small": "536b0662742c02347bc0e980a01041f333bce120",
    "Systran/faster-whisper-medium": "08e178d48790749d25932bbc082711ddcfdfbc4f",
    "Systran/faster-whisper-large-v3": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
    "ggerganov/whisper.cpp": "5359861c739e955e79d9a303bcbc70fb988958b1",
    "Qwen/Qwen2.5-1.5B-Instruct-GGUF": "91cad51170dc346986eccefdc2dd33a9da36ead9",
}

# The Hub's own SHA256 of each file (its Git LFS id) at the commit above.
SHA256 = {
    ("Systran/faster-whisper-tiny", "model.bin"):
        "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1",
    ("Systran/faster-whisper-base", "model.bin"):
        "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9",
    ("Systran/faster-whisper-small", "model.bin"):
        "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
    ("Systran/faster-whisper-medium", "model.bin"):
        "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae",
    ("Systran/faster-whisper-large-v3", "model.bin"):
        "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1",
    ("ggerganov/whisper.cpp", "ggml-tiny-q8_0.bin"):
        "c2085835d3f50733e2ff6e4b41ae8a2b8d8110461e18821b09a15c40c42d1cca",
    ("ggerganov/whisper.cpp", "ggml-base-q8_0.bin"):
        "c577b9a86e7e048a0b7eada054f4dd79a56bbfa911fbdacf900ac5b567cbb7d9",
    ("ggerganov/whisper.cpp", "ggml-small-q8_0.bin"):
        "49c8fb02b65e6049d5fa6c04f81f53b867b5ec9540406812c643f177317f779f",
    ("ggerganov/whisper.cpp", "ggml-medium-q5_0.bin"):
        "19fea4b380c3a618ec4723c3eef2eb785ffba0d0538cf43f8f235e7b3b34220f",
    ("ggerganov/whisper.cpp", "ggml-large-v3-q5_0.bin"):
        "d75795ecff3f83b5faa89d1900604ad8c780abd5739fae406de19f23ecd98ad1",
    ("Qwen/Qwen2.5-1.5B-Instruct-GGUF", "qwen2.5-1.5b-instruct-q4_k_m.gguf"):
        "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e",
}


def revision(repo):
    """The commit TuxTalk takes *repo* at, or None (whatever the cache has)."""
    return REVISIONS.get(repo)
