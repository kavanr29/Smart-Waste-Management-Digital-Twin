MAPPINGS = {
    'plastic_bottle':'Plastic','bottle':'Plastic','plastic':'Plastic','plastic_bag':'Plastic',
    'paper':'Paper','cardboard':'Cardboard','glass':'Glass','metal':'Metal','metal_can':'Metal','can':'Metal','aluminium_can':'Metal',
    'banana-peel':'Organic','banana_peel':'Organic','orange-peel':'Organic','orange_peel':'Organic','organic':'Organic','food':'Organic','food_waste':'Organic','e-waste':'E-waste','battery':'E-waste','hazardous':'Hazardous','styrofoam':'Styrofoam'
}
WEIGHT_ESTIMATES_KG = {'Plastic':0.025,'Paper':0.020,'Cardboard':0.100,'Metal':0.015,'Glass':0.200,'Organic':0.150,'E-waste':0.250,'Hazardous':0.100,'Styrofoam':0.050,'Other':0.050,'Mixed':0.050}
def map_category(label: str) -> str:
    normalized=label.lower().replace(' ','_').strip()
    return MAPPINGS.get(normalized, next((v for k,v in MAPPINGS.items() if k in normalized),'Other'))
def is_recyclable(category: str) -> bool: return category in {'Plastic','Paper','Glass','Metal','Cardboard','E-waste'}
def estimated_weight_kg(category: str) -> float: return WEIGHT_ESTIMATES_KG.get(category,0.050)
