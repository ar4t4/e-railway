"""Simulate site transitions around the real booking coordinator, without network."""
from types import SimpleNamespace
import pytest
from companion import desktop
from companion.desktop import Browser, NeedsAttention


class Control:
    def __init__(self, click=lambda:None):
        self.action = click
        self.text = "SNIGDHA Available"
        self.value = ""
    def click(self):
        self.action()
    def clear(self):
        self.value = ""
    def send_keys(self, value):
        self.value += value
    def is_enabled(self):
        return True
    def find_elements(self, by, selector):
        return [Control()]


class FakeBrowser(Browser):
    def __init__(self, directory, seat_transition="selected", available=3, otp=False):
        super().__init__(directory, lambda:True)
        self.driver = SimpleNamespace(current_url="https://eticket.railway.gov.bd/booking/train/search",
            get_log=lambda _:[], get=lambda _:None, execute_script=lambda _:False)
        self.states = {str(i):"available" for i in range(available)}
        self.transition = seat_transition
        self.otp = otp
        self.steps = []
    def session(self):
        return {"token":"test"}
    def browser_route(self, result):
        return "route1"
    def choose_card(self, train):
        return Control()
    def visible(self, selector):
        return []
    def switch_coach(self, value):
        self.check()
    def read_grid(self, coach):
        def button(i):
            return Control(lambda:self.states.update({i:self.transition}))
        return [{"id":i,"route":"route1","seat":"GA-"+i,"coach":"","state":s,"button":button(i)} for i,s in self.states.items()]
    def wait_visible(self, selector, timeout=20):
        self.steps.append(selector)
        if selector == "#bKash_button" and self.otp:
            raise NeedsAttention("Verification required")
        return Control()


def job(action="payment"):
    return {"action":action,"passengers":2,"passenger_details":[{"name":"Test One"},{"name":"Test Two"}],
        "result":{"url":"https://eticket.railway.gov.bd/", "train":"TEST", "class":"SNIGDHA"}}


def test_full_selection_proceeds_to_verified_payment_control(tmp_path):
    b = FakeBrowser(tmp_path)
    status, _ = b.book(job())
    assert status == "payment_ready"
    assert len(b.selected) == 2
    assert b.steps.index("#pname0") < b.steps.index("#confirm_button") < b.steps.index("#bKash_button")


def test_prepare_seats_never_confirms_passengers(tmp_path):
    b = FakeBrowser(tmp_path)
    assert b.book(job("seats"))[0] == "seats_selected"
    assert "#confirm_button" not in b.steps


def test_insufficient_group_takes_no_seats(tmp_path):
    b = FakeBrowser(tmp_path, available=1)
    with pytest.raises(NeedsAttention, match="Not enough"):
        b.book(job())
    assert b.selected == []
    assert set(b.states.values()) == {"available"}


def test_someone_elses_hold_is_not_treated_as_our_selection(tmp_path, monkeypatch):
    ticks = iter(range(1000))
    monkeypatch.setattr(desktop, "time", SimpleNamespace(time=lambda:next(ticks),sleep=lambda _:None))
    b = FakeBrowser(tmp_path, seat_transition="held")
    with pytest.raises(NeedsAttention, match="could not be confirmed"):
        b.book(job())
    assert b.selected == []
    assert "#confirm_button" not in b.steps


def test_otp_interruption_is_not_payment_ready(tmp_path):
    b = FakeBrowser(tmp_path, otp=True)
    with pytest.raises(NeedsAttention, match="Verification"):
        b.book(job())
    assert len(b.selected) == 2
