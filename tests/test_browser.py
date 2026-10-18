import pytest
from companion.desktop import Browser, NeedsAttention


class Driver:
    current_url = "https://eticket.railway.gov.bd/booking/train/search"


class Button:
    def __init__(self):
        self.clicked = 0
    def click(self):
        self.clicked += 1


def test_mode_permission_checked_before_every_browser_mutation(tmp_path):
    browser = Browser(tmp_path, lambda:False)
    browser.driver = Driver()
    button = Button()
    with pytest.raises(NeedsAttention):
        browser.click(button)
    assert button.clicked == 0


def test_offsite_page_never_receives_automatic_click(tmp_path):
    browser = Browser(tmp_path, lambda:True)
    browser.driver = Driver()
    browser.driver.current_url = "https://payment.example/"
    button = Button()
    with pytest.raises(NeedsAttention):
        browser.click(button)
    assert button.clicked == 0


def test_uncertain_click_is_not_retried(tmp_path):
    browser = Browser(tmp_path, lambda:True)
    browser.driver = Driver()
    class FailingButton(Button):
        def click(self):
            self.clicked += 1
            raise RuntimeError("timeout after server may have accepted selection")
    button = FailingButton()
    with pytest.raises(RuntimeError):
        browser.click(button)
    assert button.clicked == 1


def test_closed_browser_releases_driver_and_returns_no_session(tmp_path):
    from selenium.common.exceptions import InvalidSessionIdException
    from unittest.mock import Mock
    service = Mock()
    class ClosedDriver:
        @property
        def current_url(self):
            raise InvalidSessionIdException('Browser closed')
    driver = ClosedDriver()
    driver.service = service
    browser = Browser(tmp_path, lambda: False)
    browser.driver = driver
    assert browser.session() is None
    assert browser.driver is None and browser.disconnected
    service.stop.assert_called_once()
