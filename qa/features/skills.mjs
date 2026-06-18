// Feature: skills. Lightweight smoke check that the MCP Skills surface loads.

export default {
  id: "skills",
  title: "AI — skills",
  async run(h) {
    await h.step("skills page loads", async () => {
      await h.goto("/dashboard/skills");
      await h.expectText(/skill/i);
    });
  },
};
