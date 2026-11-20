"""
Frontend Component Tests
"""
import pytest
from playwright.sync_api import Page, expect


class TestPredictionsPage:
    """Test predictions page."""
    
    def test_predictions_page_loads(self, page: Page):
        """Test that predictions page loads successfully."""
        page.goto("/predictions")
        expect(page.locator("h1")).to_contain_text("Quantum Prediction Vector")
    
    def test_predictions_show_nifty(self, page: Page):
        """Test that NIFTY predictions are displayed."""
        page.goto("/predictions")
        expect(page.locator("text=NIFTY 50")).to_be_visible()
    
    def test_predictions_show_banknifty(self, page: Page):
        """Test that BANKNIFTY predictions are displayed."""
        page.goto("/predictions")
        expect(page.locator("text=BANKNIFTY")).to_be_visible()


class TestNewsPage:
    """Test news page."""
    
    def test_news_page_loads(self, page: Page):
        """Test that news page loads successfully."""
        page.goto("/news")
        expect(page.locator("h1")).to_contain_text("Market Intelligence")
    
    def test_news_articles_load(self, page: Page):
        """Test that news articles are loaded."""
        page.goto("/news")
        # Wait for articles to load
        expect(page.locator("article").first).to_be_visible(timeout=10000)


class TestTradingPage:
    """Test trading page."""
    
    def test_trading_page_loads(self, page: Page):
        """Test that trading page loads successfully."""
        page.goto("/trading")
        expect(page.locator("h1")).to_contain_text("Quantum Trading Terminal")
    
    def test_order_entry_form_exists(self, page: Page):
        """Test that order entry form is present."""
        page.goto("/trading")
        expect(page.locator("input[placeholder='Quantity']")).to_be_visible()
        expect(page.locator("button").filter(has_text="BUY")).to_be_visible()
        expect(page.locator("button").filter(has_text="SELL")).to_be_visible()


class TestConnectionStatus:
    """Test connection status badge."""
    
    def test_connection_badge_visible(self, page: Page):
        """Test that connection status badge is visible."""
        page.goto("/predictions")
        # The ConnectionStatusBadge component should render
        expect(page.locator("text=LIVE DATA")).to_be_visible()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
