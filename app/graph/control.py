"""控制工具：只把可选项交给用户，不做业务动作。"""

from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator


class OfferHumanOptionsArgs(BaseModel):
    options: list[Literal["handoff", "ticket"]] = Field(
        min_length=1, max_length=2,
        description="给用户的可选项：handoff 为转人工，ticket 为建工单。可以只给一个",
    )
    ticket_description: str | None = Field(
        None, min_length=1, max_length=500, description="options 含 ticket 时必填，概括用户诉求")
    ticket_type: Literal["售后", "投诉", "咨询"] | None = Field(
        None, description="options 含 ticket 时必填")

    @model_validator(mode="after")
    def _check(self):
        if len(set(self.options)) != len(self.options):
            raise ValueError("options 不能重复")
        if "ticket" in self.options and (not self.ticket_description or not self.ticket_type):
            raise ValueError("options 含 ticket 时必须填写 ticket_description 和 ticket_type")
        return self


@tool("offer_human_options", args_schema=OfferHumanOptionsArgs)
async def offer_human_options(
    options: list[str], ticket_description: str | None = None, ticket_type: str | None = None
) -> dict:
    """在回复下方给用户展示「转人工」「建工单」按钮，由用户自己选择。只展示选项，不会转接，也不会创建工单。"""
    return {"shown": list(options)}


def actions_from_args(args: dict) -> list[dict]:
    parsed = OfferHumanOptionsArgs.model_validate(args)
    actions = []
    for option in parsed.options:
        if option == "handoff":
            actions.append({"type": "handoff"})
        else:
            actions.append({"type": "ticket", "description": parsed.ticket_description,
                            "ticket_type": parsed.ticket_type})
    return actions
