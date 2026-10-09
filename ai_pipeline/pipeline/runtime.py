"""Builds the real clients once per Lambda container. Tests build a Runtime from fakes and call set_runtime()."""
import json
import time
from dataclasses import dataclass, field
from typing import Callable

from .bedrock_jobs import Jobs
from .config import Settings
from .pgdb import Db
from .prompts import PromptBook
from .s3io import Store
from .snowflake_io import Snowflake


@dataclass
class Runtime:
    settings: Settings
    db: Db
    store: Store
    jobs: Jobs
    prompts: PromptBook
    lambda_client: object
    snowflake_factory: Callable
    clock: Callable[[], float] = time.time
    completion_hook: Callable | None = None                    # tests: run the completion handler without a Lambda
    _snowflake: Snowflake | None = field(default=None, repr=False)

    @property
    def snowflake(self) -> Snowflake:
        if self._snowflake is None:                            # connect only when something needs Snowflake
            self._snowflake = self.snowflake_factory()
        return self._snowflake

    def invoke_completion(self, payload: dict) -> None:
        """Hand a finished direct run to the completion Lambda (asynchronously, with its own time budget)."""
        if self.completion_hook:
            self.completion_hook(payload)
        else:
            self.lambda_client.invoke(FunctionName=self.settings.completion_function, InvocationType="Event", Payload=json.dumps(payload).encode())


_RUNTIME: Runtime | None = None


def set_runtime(rt: Runtime | None) -> None:
    global _RUNTIME
    _RUNTIME = rt


def get_runtime() -> Runtime:
    global _RUNTIME
    if _RUNTIME is None:
        import boto3
        from botocore.config import Config

        settings = Settings.from_env()
        retry = Config(retries={"max_attempts": 8, "mode": "adaptive"})
        slow = Config(retries={"max_attempts": 8, "mode": "adaptive"}, read_timeout=120)
        secrets = boto3.client("secretsmanager", config=retry)
        store = Store(boto3.client("s3", config=retry), settings.bucket)
        _RUNTIME = Runtime(
            settings=settings, db=Db.connect(secrets, settings), store=store,
            jobs=Jobs(boto3.client("bedrock", config=retry), boto3.client("bedrock-runtime", config=slow), store, settings),
            prompts=PromptBook(boto3.client("bedrock-agent", config=retry), settings), lambda_client=boto3.client("lambda", config=retry),
            snowflake_factory=lambda: Snowflake.connect(secrets, settings))
    return _RUNTIME
