/// <reference types="cypress" />

const GUILD_ID = Cypress.env('GUILD_ID');
const ADMIN_TOKEN = Cypress.env('ADMIN_TOKEN');

function pad(n) {
  return String(n).padStart(2, '0');
}

function utcToLocal(utcTime) {
  const [h, m] = utcTime.split(':').map(Number);
  const d = new Date();
  d.setUTCHours(h, m, 0, 0);
  return pad(d.getHours()) + ':' + pad(d.getMinutes());
}

function localToUTC(localTime) {
  const [h, m] = localTime.split(':').map(Number);
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes());
}

describe('Admin Schedule Save with Timezone Round-Trip', () => {
  beforeEach(() => {
    // cy.session() automatically restores the session cookie
    cy.login(ADMIN_TOKEN);
    cy.visit(`/prayers/${GUILD_ID}`);
    // Verify we are on the admin page, not redirected to login
    cy.url().should('include', `/prayers/${GUILD_ID}`);
    cy.get('#schedule-form', { timeout: 10000 }).should('be.visible');
  });

  it('should load the admin schedule page after login', () => {
    cy.contains('Save Schedule').should('be.visible');
    cy.contains('h1', 'Devotional Non-Duality').should('be.visible');
  });

  it('should display all 7 days with 3 slots each', () => {
    const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    days.forEach((day) => {
      cy.contains('.text-sm.font-semibold', day).should('be.visible');
    });
    cy.get('input[type="time"]').should('have.length', 21);
  });

  it('should display timezone detection info', () => {
    cy.get('#tz-info').should('be.visible').and('not.have.text', 'detecting...');
  });

  it('should round-trip: save a time and verify it persists correctly', () => {
    cy.get('input[type="time"]').first().as('firstInput');

    cy.get('@firstInput').invoke('attr', 'data-utc').then((originalUTC) => {
      const newLocalTime = '09:45';
      const expectedUTC = localToUTC(newLocalTime);

      cy.log(`Original UTC: ${originalUTC}, new local: ${newLocalTime}, expected UTC: ${expectedUTC}`);

      cy.get('@firstInput').clear().type(newLocalTime);
      cy.get('#schedule-form').submit();

      cy.url().should('include', `/prayers/${GUILD_ID}`);
      cy.contains('Saved').should('be.visible');

      cy.get('input[type="time"]').first().invoke('val').then((displayedTime) => {
        cy.log(`Displayed after save: ${displayedTime}`);
        expect(displayedTime).to.eq(newLocalTime);
      });
    });
  });

  it('should round-trip: change all three Monday slots and verify', () => {
    const newTimes = ['10:00', '14:30', '20:15'];

    cy.get('input[type="time"]').eq(0).clear().type(newTimes[0]);
    cy.get('input[type="time"]').eq(1).clear().type(newTimes[1]);
    cy.get('input[type="time"]').eq(2).clear().type(newTimes[2]);
    cy.get('input[type="checkbox"]').eq(0).check();
    cy.get('input[type="checkbox"]').eq(1).check();
    cy.get('input[type="checkbox"]').eq(2).check();

    cy.get('#schedule-form').submit();
    cy.url().should('include', `/prayers/${GUILD_ID}`);
    cy.contains('Saved').should('be.visible');

    cy.get('input[type="time"]').eq(0).invoke('val').should('eq', newTimes[0]);
    cy.get('input[type="time"]').eq(1).invoke('val').should('eq', newTimes[1]);
    cy.get('input[type="time"]').eq(2).invoke('val').should('eq', newTimes[2]);
  });

  it('should persist saved times on page reload', () => {
    const testTime = '11:30';

    cy.get('input[type="time"]').first().clear().type(testTime);
    cy.get('input[type="checkbox"]').first().check();
    cy.get('#schedule-form').submit();
    cy.contains('Saved').should('be.visible');

    cy.reload();
    cy.url().should('include', `/prayers/${GUILD_ID}`);
    cy.get('#schedule-form', { timeout: 10000 }).should('be.visible');
    cy.get('input[type="time"]').first().invoke('val').should('eq', testTime);
  });

  it('should show saved times on the public schedule page', () => {
    const testTime = '12:00';

    cy.get('input[type="time"]').first().clear().type(testTime);
    cy.get('#schedule-form').submit();
    cy.contains('Saved').should('be.visible');

    cy.visit(`/prayers/public/${GUILD_ID}`);
    cy.contains('Prayer Schedule').should('be.visible');
    cy.get('[data-utc-time]').should('have.length.greaterThan', 0);
    cy.get('[data-utc-time] .local-time').should('not.contain', '--:--');
  });

  it('should handle duplicate time validation', () => {
    const dupTime = '13:13';

    cy.get('input[type="time"]').eq(0).clear().type(dupTime);
    cy.get('input[type="time"]').eq(1).clear().type(dupTime);

    cy.get('#schedule-form').submit();
    cy.contains('Duplicate').should('be.visible');
  });

  it('should have working client-side timezone conversion', () => {
    cy.get('input[type="time"]').first().then(($inp) => {
      const utcVal = $inp.attr('data-utc');
      const displayedVal = $inp.val();
      cy.log(`data-utc: ${utcVal}, displayed: ${displayedVal}`);
      if (utcVal && displayedVal) {
        const expectedLocal = utcToLocal(utcVal);
        expect(displayedVal).to.eq(expectedLocal);
      }
    });
  });
});
