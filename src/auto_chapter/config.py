"""Configuration models and loader."""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, PrivateAttr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(ValueError):
    """config.yml or its .env cannot be used.

    `problem` says what is wrong and where, `advice` the one thing to do; both
    are written for the user (cli.py prints them as they are).
    """

    def __init__(self, problem: str, advice: str) -> None:
        super().__init__(f"{problem}\n{advice}")
        self.problem = problem
        self.advice = advice


_FIX_THE_LINE = "Fix this line in the file, then start auto-chapter again."


def _problem_in(path: Path, lines: list[str]) -> str:
    return f"There is a problem in config.yml ({path}):\n" + "\n".join(
        f"  {ln}" for ln in lines
    )


def _where(loc: tuple[int | str, ...]) -> str:
    """("paths", 0, "local") -> "paths, entry 1, local"."""
    return ", ".join(f"entry {p + 1}" if isinstance(p, int) else p for p in loc)


def default_config_path() -> Path:
    """config.yml in ~/.config/auto-chapter/ ($XDG_CONFIG_HOME/auto-chapter/ if set).

    Outside the app's own folder on purpose: updating means deleting that folder
    and unpacking the new version, and the settings and the token must survive it.
    A relative $XDG_CONFIG_HOME is invalid per the XDG spec and is ignored.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".config"
    return base / "auto-chapter" / "config.yml"


class PlexConfig(BaseModel):
    url: str
    token: str = ""  # filled from env


class PathMapping(BaseModel):
    """One Plex folder and where this computer reaches it.

    Paths are checked (and a missing trailing "/" added) as soon as a mapping is
    built, so a hand-edited config.yml cannot smuggle in a path that would
    translate movie paths wrongly: without the final "/", "/plex/Filmek" would
    also match "/plex/Filmek2/...", and a local "/mnt/nas/Filmek" would turn
    "/plex/Filmek/Heat/Heat.mkv" into "/mnt/nas/FilmekHeat/Heat.mkv".
    """

    plex: str
    local: str
    enabled: bool = True

    @field_validator("plex")
    @classmethod
    def _plex_path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("the Plex path is empty")
        return value if value.endswith("/") else value + "/"

    @field_validator("local")
    @classmethod
    def _local_path(cls, value: str) -> str:
        value = os.path.expanduser(value.strip())
        if not value.startswith("/"):
            raise ValueError(
                f"the path on this computer must start with '/' (or '~/'), got '{value}'"
            )
        return value if value.endswith("/") else value + "/"


class DetectionConfig(BaseModel):
    min_gap_seconds: int = Field(default=300, gt=0)
    end_margin_seconds: int = Field(default=900, ge=0)
    min_duration_seconds: int = Field(default=1800, gt=0)
    chapter_threshold: int = Field(default=4, ge=1, le=99)
    black_duration: float = Field(default=0.5, gt=0)
    black_pix_threshold: float = Field(default=0.10, ge=0, le=1)
    black_pic_threshold: float = Field(default=0.98, ge=0, le=1)
    scdet_threshold: int = Field(default=10, ge=1, le=100)


class ProcessingConfig(BaseModel):
    batch_size: int = Field(default=50, gt=0)
    check_active_streams: bool = True


class ChapterConfig(BaseModel):
    # The chapter names are fixed (models.AUTO_CHAPTER_PREFIX and
    # CREDITS_CHAPTER_NAME). Older config.yml files still have "prefix" and
    # "credits_label" here; unknown keys are ignored.
    language: str = "und"


class LoggingConfig(BaseModel):
    log_level: str = "WARNING"
    file_path: str = ""


class Config(BaseSettings):
    """Top-level config. Loaded from config.yml + the .env next to it."""

    # No env_file here: load() passes the .env next to config.yml, so the
    # current directory never matters.
    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

    plex: PlexConfig
    paths: list[PathMapping]
    detection: DetectionConfig = DetectionConfig()
    processing: ProcessingConfig = ProcessingConfig()
    chapter: ChapterConfig = ChapterConfig()
    logging: LoggingConfig = LoggingConfig()

    # Override from env
    plex_token: str = ""

    # The config.yml this was loaded from. Settings saves go back here, and
    # .env and a relative log file path are taken from the same folder.
    _path: Path = PrivateAttr(default_factory=default_config_path)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def env_path(self) -> Path:
        return self._path.parent / ".env"

    def log_file_path(self) -> Path | None:
        """The log file; a relative logging.file_path is inside the settings folder."""
        if not self.logging.file_path:
            return None
        return self._path.parent / os.path.expanduser(self.logging.file_path)

    @classmethod
    def load(cls, config_path: Path | str | None = None) -> "Config":
        """Load config.yml (default: default_config_path()) + the .env next to it."""
        path = Path(config_path) if config_path is not None else default_config_path()
        path = path.expanduser().absolute()  # messages show the full path
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(_problem_in(path, str(e).splitlines()), _FIX_THE_LINE) from None

        if data is None:
            raise ConfigError(
                f"config.yml is empty: {path}",
                "Run ./install.sh in the auto-chapter folder to set it up again.",
            )
        if not isinstance(data, dict):
            raise ConfigError(_problem_in(path, ["it holds no settings"]), _FIX_THE_LINE)

        # A bare "paths:" key with nothing under it parses as YAML null.
        # Treat that the same as "no folders mapped yet" rather than crashing --
        # the app is designed to run with an empty mapping list.
        if data.get("paths") is None:
            data["paths"] = []

        try:
            config = cls(_env_file=path.parent / ".env", **data)  # type: ignore[call-arg]
        except ValidationError as e:
            lines = [
                f"{_where(err['loc'])}: {err['msg'].removeprefix('Value error, ')}"
                for err in e.errors()
            ]
            raise ConfigError(_problem_in(path, lines), _FIX_THE_LINE) from None
        config._path = path
        if config.plex_token:
            config.plex.token = config.plex_token
        if not config.plex.token:
            raise ConfigError(
                f"No Plex token found in {config.env_path}.",
                "Run ./install.sh in the auto-chapter folder, or put the line "
                "PLEX_TOKEN=<your token> into that file.",
            )

        return config
