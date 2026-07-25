import { STORIES, getStory, type Story } from "./mock-data";

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

export const api = {
  listStories: async (): Promise<Story[]> => {
    await delay(120);
    return STORIES;
  },
  getStory: async (id: string): Promise<Story> => {
    await delay(80);
    return getStory(id);
  },
  createEpisode: async (_payload: unknown): Promise<{ id: string }> => {
    await delay(200);
    return { id: "st_2" }; // route new uploads into the processing demo
  },
};
