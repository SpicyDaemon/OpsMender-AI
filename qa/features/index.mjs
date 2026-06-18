// Ordered list of QA features. Order matters: teams → services/escalation/
// rosters depend on the team; incidents prefer the created service; logout
// runs last.

import auth from "./auth.mjs";
import teams from "./teams.mjs";
import services from "./services.mjs";
import escalation from "./escalation.mjs";
import rosters from "./rosters.mjs";
import rosterCalendar from "./roster_calendar.mjs";
import notifications from "./notifications.mjs";
import incidents from "./incidents.mjs";
import reliability from "./reliability.mjs";
import models from "./models.mjs";
import skills from "./skills.mjs";
import logout from "./logout.mjs";

export const features = [
  auth,
  teams,
  services,
  escalation,
  rosters,
  rosterCalendar,
  notifications,
  incidents,
  reliability,
  models,
  skills,
  logout,
];
