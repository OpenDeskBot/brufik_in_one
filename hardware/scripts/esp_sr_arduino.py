"""Wire esp-sr (IDF component) into the Arduino build: headers, sources, prebuilt libs."""
Import("env")
import os

PROJECT_DIR = env["PROJECT_DIR"]
ESP_SR = os.path.join(PROJECT_DIR, "components", "esp-sr")
ESP_SR_LIB = os.path.join(ESP_SR, "lib", "esp32s3")
ESP_SR_SRC = os.path.join(ESP_SR, "src")

if not os.path.isdir(ESP_SR):
    print("[esp-sr] components/esp-sr missing — first build runs scripts/fetch_esp_sr.py")
else:
    env.Append(CPPPATH=[
        os.path.join(ESP_SR, "include", "esp32s3"),
        os.path.join(ESP_SR, "src", "include"),
        os.path.join(ESP_SR, "esp-tts", "esp_tts_chinese", "include"),
    ])

    env.BuildSources(
        os.path.join("$BUILD_DIR", "esp_sr"),
        ESP_SR_SRC,
    )

    env.Append(LIBPATH=[ESP_SR_LIB])
    env.Append(LIBS=[
        "esp_audio_front_end",
        "esp_audio_processor",
        "vadnet",
        "wakenet",
        "nsnet",
        "multinet",
        "dl_lib",
        "c_speech_features",
        "fst",
        "flite_g2p",
        "hufzip",
    ])

    print("[esp-sr] Arduino integration OK:", ESP_SR)
