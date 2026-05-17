from django.test import SimpleTestCase

from recipes.markdown import render_recipe_markdown


class RecipeMarkdownTests(SimpleTestCase):
    def test_renders_basic_formatting(self):
        html = render_recipe_markdown("**Bold** and *italic*")

        self.assertIn("<strong>Bold</strong>", html)
        self.assertIn("<em>italic</em>", html)

    def test_renders_lists_and_links(self):
        html = render_recipe_markdown(
            "- apples\n- pears\n\nSee [notes](https://example.com/tips)."
        )

        self.assertIn("<ul>", html)
        self.assertIn("<li>apples</li>", html)
        self.assertIn('href="https://example.com/tips"', html)

    def test_preserves_single_line_breaks(self):
        html = render_recipe_markdown("Heat pan\nAdd batter")

        self.assertIn("Heat pan", html)
        self.assertIn("Add batter", html)
        self.assertIn("<br", html)

    def test_strips_unsafe_html(self):
        html = render_recipe_markdown('<script>alert("x")</script>\n\nSafe text')

        self.assertNotIn("script", html.lower())
        self.assertIn("Safe text", html)
