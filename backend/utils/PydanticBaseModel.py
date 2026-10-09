from pydantic import BaseModel

class PydanticBaseModel(BaseModel):
    def __hash__(self):  # make hashable BaseModel subclass
        return hash((type(self),) + tuple(self.__dict__.values()))
