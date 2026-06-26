from pras_bot.config_loader import load_config


def test_load_config_reads_packaged_defaults(tmp_path):
    local_config = tmp_path / "pras-bot.yml"
    local_config.write_text("", encoding="utf-8")

    config = load_config(str(local_config), "token", "owner/repo")

    assert config["labels"][0]["name"] == "likely-spam"
