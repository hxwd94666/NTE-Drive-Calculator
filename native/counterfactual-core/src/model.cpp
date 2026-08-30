#include "counterfactual/model.hpp"

#include <cmath>
#include <limits>
#include <set>
#include <stdexcept>
#include <tuple>

namespace counterfactual {
namespace {

using Object = json::Value::Object;

const json::Value& field(const Object& object, const std::string& key) {
    const auto found = object.find(key);
    if (found == object.end()) throw std::runtime_error("missing field: " + key);
    return found->second;
}

const Object& object_field(const Object& object, const std::string& key) {
    const auto& value = field(object, key);
    if (!value.is_object()) throw std::runtime_error(key + " must be an object");
    return value.as_object();
}

const json::Value::Array& array_field(const Object& object, const std::string& key) {
    const auto& value = field(object, key);
    if (!value.is_array()) throw std::runtime_error(key + " must be an array");
    return value.as_array();
}

std::string string_field(const Object& object, const std::string& key) {
    const auto& value = field(object, key);
    if (!value.is_string()) throw std::runtime_error(key + " must be a string");
    return value.as_string();
}

double number_field(const Object& object, const std::string& key) {
    const auto& value = field(object, key);
    if (!value.is_number()) throw std::runtime_error(key + " must be a number");
    return value.as_number();
}

int int_field(const Object& object, const std::string& key) {
    const double value = number_field(object, key);
    if (std::floor(value) != value || value < std::numeric_limits<int>::min() ||
        value > std::numeric_limits<int>::max()) {
        throw std::runtime_error(key + " must be an integer");
    }
    return static_cast<int>(value);
}

std::int64_t int64_field(const Object& object, const std::string& key) {
    const double value = number_field(object, key);
    if (std::floor(value) != value ||
        std::abs(value) > static_cast<double>(std::numeric_limits<std::int64_t>::max())) {
        throw std::runtime_error(key + " must be an int64");
    }
    return static_cast<std::int64_t>(value);
}

std::optional<double> optional_number(const Object& object, const std::string& key) {
    const auto& value = field(object, key);
    if (value.is_null()) return std::nullopt;
    if (!value.is_number()) throw std::runtime_error(key + " must be number or null");
    return value.as_number();
}

std::optional<int> optional_int(const Object& object, const std::string& key) {
    const auto& value = field(object, key);
    if (value.is_null()) return std::nullopt;
    return int_field(object, key);
}

json::Value optional_value(const std::optional<double>& value) {
    return value ? json::Value(*value) : json::Value(nullptr);
}

json::Value strings(const std::vector<std::string>& values) {
    json::Value::Array result;
    for (const auto& value : values) result.emplace_back(value);
    return result;
}

}  // namespace

Request request_from_json(const json::Value& value) {
    if (!value.is_object()) throw std::runtime_error("request must be an object");
    const auto& root = value.as_object();
    Request request;
    request.schema_version = string_field(root, "schema_version");
    request.model_version = string_field(root, "model_version");
    request.dataset_version = string_field(root, "dataset_version");
    const auto& axis = object_field(root, "axis");
    request.axis.complete = field(axis, "complete").as_bool();
    request.axis.range_start_us = int64_field(axis, "range_start_us");
    request.axis.range_end_us = int64_field(axis, "range_end_us");

    for (const auto& value_row : array_field(root, "characters")) {
        const auto& row = value_row.as_object();
        Character character;
        character.character_id = int_field(row, "character_id");
        character.character_level = number_field(row, "character_level");
        for (const auto& [key, stat] : object_field(row, "stats")) {
            if (!stat.is_number()) throw std::runtime_error("character stat must be numeric");
            character.stats.emplace(key, stat.as_number());
        }
        request.characters.push_back(std::move(character));
    }
    for (const auto& value_row : array_field(root, "target_profiles")) {
        const auto& row = value_row.as_object();
        TargetProfile profile;
        profile.scope_half = string_field(row, "scope_half");
        profile.target_id = string_field(row, "target_id");
        profile.damage_attribute = string_field(row, "damage_attribute");
        profile.resistance = number_field(row, "resistance");
        profile.enemy_defense_base = optional_number(row, "enemy_defense_base");
        request.target_profiles.push_back(std::move(profile));
    }
    for (const auto& value_row : array_field(root, "hits")) {
        const auto& row = value_row.as_object();
        Hit hit;
        hit.event_id = string_field(row, "event_id");
        hit.sequence = int_field(row, "sequence");
        hit.relative_time_us = int64_field(row, "relative_time_us");
        hit.scope_half = string_field(row, "scope_half");
        hit.target_id = string_field(row, "target_id");
        hit.character_id = optional_int(row, "character_id");
        hit.damage = number_field(row, "damage");
        hit.direction = string_field(row, "direction");
        hit.classification = string_field(row, "classification");
        hit.damage_attribute = string_field(row, "damage_attribute");
        hit.scaling_property_id = string_field(row, "scaling_property_id");
        hit.critical_policy = string_field(row, "critical_policy");
        hit.critical_state = string_field(row, "critical_state");
        hit.critical_rate = optional_number(row, "critical_rate");
        request.hits.push_back(std::move(hit));
    }
    for (const auto& value_buff : array_field(root, "buffs")) {
        const auto& buff_row = value_buff.as_object();
        BuffGroup buff;
        buff.buff_key = string_field(buff_row, "buff_key");
        for (const auto& value_interval : array_field(buff_row, "intervals")) {
            const auto& row = value_interval.as_object();
            Interval interval;
            interval.interval_id = string_field(row, "interval_id");
            interval.start_us = int64_field(row, "start_us");
            interval.end_us = int64_field(row, "end_us");
            interval.scope_half = string_field(row, "scope_half");
            interval.source_character_id = int_field(row, "source_character_id");
            interval.target_scope = string_field(row, "target_scope");
            interval.target_id = string_field(row, "target_id");
            for (const auto& value_modifier : array_field(row, "modifiers")) {
                const auto& modifier_row = value_modifier.as_object();
                interval.modifiers.push_back(Modifier{
                    string_field(modifier_row, "property_id"),
                    string_field(modifier_row, "operation"),
                    optional_number(modifier_row, "value"),
                    string_field(modifier_row, "value_confidence"),
                    string_field(modifier_row, "calculation_asset_path"),
                });
            }
            buff.intervals.push_back(std::move(interval));
        }
        request.buffs.push_back(std::move(buff));
    }
    validate_request(request);
    return request;
}

void validate_request(const Request& request) {
    if (request.schema_version != "counterfactual-request-v1") {
        throw std::runtime_error("unsupported request schema_version");
    }
    if (request.model_version.empty() || request.dataset_version.empty()) {
        throw std::runtime_error("model_version and dataset_version are required");
    }
    if (request.axis.range_start_us >= request.axis.range_end_us) {
        throw std::runtime_error("axis range must be left-closed/right-open and non-empty");
    }
    std::set<int> character_ids;
    for (const auto& character : request.characters) {
        if (!character_ids.insert(character.character_id).second) throw std::runtime_error("duplicate character_id");
        if (!std::isfinite(character.character_level)) throw std::runtime_error("non-finite character level");
        for (const auto& [_, stat] : character.stats) if (!std::isfinite(stat)) throw std::runtime_error("non-finite stat");
    }
    std::set<std::tuple<std::string, std::string, std::string>> target_profile_keys;
    for (const auto& profile : request.target_profiles) {
        if (!target_profile_keys.emplace(
                profile.scope_half, profile.target_id, profile.damage_attribute).second) {
            throw std::runtime_error(
                "duplicate target profile scope_half/target_id/damage_attribute");
        }
        if (profile.damage_attribute.empty()) {
            throw std::runtime_error("target profile damage_attribute is required");
        }
        if (!std::isfinite(profile.resistance) ||
            (profile.enemy_defense_base && !std::isfinite(*profile.enemy_defense_base))) {
            throw std::runtime_error("non-finite target profile value");
        }
        if (profile.enemy_defense_base && *profile.enemy_defense_base < 0.0) {
            throw std::runtime_error("target profile enemy_defense_base must be non-negative");
        }
    }
    std::set<std::string> event_ids;
    for (const auto& hit : request.hits) {
        if (hit.event_id.empty() || !event_ids.insert(hit.event_id).second) throw std::runtime_error("duplicate or empty event_id");
        if (!std::isfinite(hit.damage) || hit.damage < 0.0) throw std::runtime_error("hit damage must be finite and non-negative");
        if (hit.critical_rate && !std::isfinite(*hit.critical_rate)) throw std::runtime_error("non-finite critical_rate");
        if (hit.character_id && !character_ids.contains(*hit.character_id)) throw std::runtime_error("hit references unknown character_id");
    }
    std::set<std::string> buff_keys;
    std::set<std::string> interval_ids;
    for (const auto& buff : request.buffs) {
        if (buff.buff_key.empty() || !buff_keys.insert(buff.buff_key).second) throw std::runtime_error("duplicate or empty buff_key");
        for (const auto& interval : buff.intervals) {
            if (interval.interval_id.empty() || !interval_ids.insert(interval.interval_id).second) throw std::runtime_error("duplicate or empty interval_id");
            if (interval.start_us >= interval.end_us) throw std::runtime_error("Buff interval must be [start_us,end_us)");
            if (interval.target_scope == "target" && interval.target_id.empty()) throw std::runtime_error("target interval requires target_id");
            for (const auto& modifier : interval.modifiers) {
                if (modifier.value && !std::isfinite(*modifier.value)) throw std::runtime_error("non-finite modifier value");
            }
        }
    }
}

json::Value response_to_json(const Response& response) {
    json::Value::Array results;
    for (const auto& result : response.results) {
        json::Value::Array hits;
        for (const auto& hit : result.hits) {
            hits.emplace_back(json::Value::Object{
                {"candidate_damage", optional_value(hit.candidate_damage)},
                {"event_id", hit.event_id},
                {"gap_codes", strings(hit.gap_codes)},
                {"quantified_ratio", optional_value(hit.quantified_ratio)},
                {"status", hit.status},
            });
        }
        results.emplace_back(json::Value::Object{
            {"basis_damage", result.basis_damage},
            {"buff_key", result.buff_key},
            {"fully_quantified_damage", result.fully_quantified_damage},
            {"gap_codes", strings(result.gap_codes)},
            {"hits", std::move(hits)},
            {"partially_quantified_damage", result.partially_quantified_damage},
            {"proven_unchanged_damage", result.proven_unchanged_damage},
            {"quantified_increment", optional_value(result.quantified_increment)},
            {"status", result.status},
            {"unavailable_damage", result.unavailable_damage},
        });
    }
    return json::Value::Object{
        {"dataset_version", response.dataset_version},
        {"model_version", response.model_version},
        {"results", std::move(results)},
        {"schema_version", response.schema_version},
    };
}

}  // namespace counterfactual
