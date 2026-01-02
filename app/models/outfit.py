from pydantic import BaseModel, Field

class OutfitSuggestion(BaseModel):
    """Structured outfit suggestion from LLM or fallback logic."""
    top: str = Field(..., description="Top clothing item suggestion")
    bottom: str = Field(..., description="Bottom clothing item suggestion")
    outerwear: str = Field(..., description="Outerwear clothing item suggestion or 'None'")
    accessories: str = Field(..., description="Accessories suggestions (comma separated)")
