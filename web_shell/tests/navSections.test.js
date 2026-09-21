import assert from 'node:assert/strict';
import test from 'node:test';
import { groupItemsIntoSections, resolveSectionLabel } from '../../chat-ui/src/navigation/navSections.js';

const item = (label, order, section = null) => ({ id: label, label, order, section });

test('a manifest declaring no sections renders as one unlabeled group', () => {
  const items = [item('Apps', 0), item('Usage', 10), item('Billing', 30)];
  const groups = groupItemsIntoSections(items);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].label, null);
  assert.deepEqual(groups[0].items.map((entry) => entry.label), ['Apps', 'Usage', 'Billing']);
});

test('sections are ordered by their lowest-ordered member, not by first appearance', () => {
  const items = [
    item('Apps', 0, 'Build'),
    item('Billing', 5, 'Money'),
    item('Setup', 10, 'Build'),
    item('Usage', 12, 'Money'),
  ];
  const groups = groupItemsIntoSections(items);
  assert.deepEqual(groups.map((group) => group.label), ['Build', 'Money']);
  assert.deepEqual(groups[0].items.map((entry) => entry.label), ['Apps', 'Setup']);
  assert.deepEqual(groups[1].items.map((entry) => entry.label), ['Billing', 'Usage']);
});

test('unsectioned items lead, ahead of every labeled section', () => {
  const items = [item('Apps', 0, 'Build'), item('Performance', 1), item('Setup', 10, 'Build')];
  const groups = groupItemsIntoSections(items);
  assert.deepEqual(groups.map((group) => group.label), [null, 'Build']);
  assert.deepEqual(groups[0].items.map((entry) => entry.label), ['Performance']);
});

test('member order inside a section is the order the caller sorted them into', () => {
  const items = [item('Apps', 0, 'Build'), item('Setup', 10, 'Build'), item('Branding', 20, 'Build')];
  const [group] = groupItemsIntoSections(items);
  assert.deepEqual(group.items.map((entry) => entry.order), [0, 10, 20]);
});

test('an empty or non-array input still yields a renderable group', () => {
  assert.deepEqual(groupItemsIntoSections([]), [{ label: null, items: [] }]);
  assert.deepEqual(groupItemsIntoSections(undefined), [{ label: null, items: [] }]);
});

test('blank and non-string section values fall back to unsectioned', () => {
  assert.equal(resolveSectionLabel({ section: '   ' }), null);
  assert.equal(resolveSectionLabel({ section: 42 }), null);
  assert.equal(resolveSectionLabel({}), null);
  assert.equal(resolveSectionLabel(undefined), null);
  assert.equal(resolveSectionLabel({ section: '  Money  ' }), 'Money');
  const groups = groupItemsIntoSections([item('Apps', 0, '  '), item('Billing', 1, 42)]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].label, null);
});

test('a section label declared with stray whitespace groups with its trimmed twin', () => {
  const groups = groupItemsIntoSections([item('Apps', 0, 'Build'), item('Setup', 1, ' Build ')]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].label, 'Build');
  assert.equal(groups[0].items.length, 2);
});
