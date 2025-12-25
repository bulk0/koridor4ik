from aiogram.fsm.state import State, StatesGroup

class DialogStates(StatesGroup):
	start = State()
	mode_choice = State()
	nl_query = State()
	nl_candidates = State()
	filter_intro = State()
	filter_collect = State()
	filter_candidates = State()
	chat = State()
	ending = State()


