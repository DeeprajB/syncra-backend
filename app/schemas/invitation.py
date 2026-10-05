from pydantic import BaseModel


class InvitationCreated(BaseModel):
    invite_url: str
