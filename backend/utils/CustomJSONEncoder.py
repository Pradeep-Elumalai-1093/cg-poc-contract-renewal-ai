from datetime import datetime
import numpy as np
from pydantic import UUID4
from json import JSONEncoder
from uuid import UUID
from fastapi.encoders import jsonable_encoder
from utils.PydanticBaseModel import PydanticBaseModel

# JSON - Numpy Parse Adapter


class CustomJSONEncoder(JSONEncoder): 

    def default(self, object):
    
        if isinstance(object, np.generic):
            return object.item()
    
        if isinstance(object, UUID4):
            return str(object)
    
        if isinstance(object, UUID):
            return str(object)

        if isinstance(object, datetime):
            return object.strftime('%Y/%m/%d %H:%M:%S')

        if isinstance(object, (PydanticBaseModel)):
            return jsonable_encoder(object)

        return super().default(object)
