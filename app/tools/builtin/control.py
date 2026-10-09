from app.graph.control import offer_human_options, offer_refund_form
from app.tools.registry import register

register(offer_human_options, max_retries=0)
register(offer_refund_form, max_retries=0)
