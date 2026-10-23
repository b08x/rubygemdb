#!/usr/bin/env ruby
# frozen_string_literal: true

$stdout.sync = true
$stderr.sync = true

require "json"
require "optparse"
require "fileutils"

begin
  gem "prism", "~> 1.0"
rescue Gem::LoadError, StandardError
  # Use default loaded prism if specified version is not available
end

require "rubydex"
require "decode"

class GraphExtractor
  attr_reader :nodes, :edges, :gems

  def initialize
    @nodes = []
    @edges = []
    @node_ids = {}
    @gems = []
  end

  def add_node(id, type, name, gem_name, data = {})
    return @node_ids[id] if @node_ids.key?(id)

    node = {
      "id" => id,
      "type" => type,
      "name" => name,
      "gem" => gem_name,
      "data" => data
    }
    @nodes << node
    @node_ids[id] = node
    node
  end

  def add_edge(source_id, target_id, type, data = {})
    return if source_id.nil? || target_id.nil? || source_id == target_id

    # Avoid duplicate edges of same source, target, type
    edge_key = "#{source_id}->#{type}->#{target_id}"
    @seen_edges ||= {}
    return if @seen_edges[edge_key]

    @seen_edges[edge_key] = true
    @edges << {
      "source" => source_id,
      "target" => target_id,
      "type" => type,
      "data" => data
    }
  end

  def find_ruby_files(gem_path)
    lib_path = File.join(gem_path, "lib")
    search_dir = Dir.exist?(lib_path) ? lib_path : gem_path

    files = Dir.glob(File.join(search_dir, "**", "*.rb")).reject do |f|
      f.include?("/spec/fixtures/") || f.include?("/test/fixtures/") ||
        f.include?("/vendor/bundle/") || f.include?("/.git/")
    end
    files.sort
  end

  def extract_gem(gem_name, gem_path)
    @gems << gem_name
    gem_node_id = "gem:#{gem_name}"
    add_node(gem_node_id, "gem", gem_name, gem_name, { "path" => gem_path })

    files = find_ruby_files(gem_path)
    return if files.empty?

    # 1. Run Rubydex for AST, definitions, hierarchy, calls
    rubydex_defs_by_qualified = {}
    begin
      graph = Rubydex::Graph.new
      graph.index_all(files)

      graph.documents.each do |doc|
        rel_file = doc.uri.to_s.sub(%r{\Afile://}, "")
        rel_file = rel_file.sub(%r{\A#{Regexp.escape(gem_path)}/?}, "")

        doc.definitions.each do |defn|
          process_rubydex_definition(gem_name, gem_node_id, defn, rel_file, rubydex_defs_by_qualified)
        end

        # Process method references (call relationships)
        doc.method_references.each do |mr|
          process_rubydex_method_reference(gem_name, mr, rel_file)
        end
      end
    rescue StandardError => e
      warn "[extract_graph.rb] Rubydex processing error on #{gem_name}: #{e.message}"
    end

    # 2. Run Decode for structured documentation, parameter types, code examples
    begin
      decode_index = Decode::Index.new
      decode_index.update(files)

      decode_index.definitions.each_value do |d_defn|
        process_decode_definition(gem_name, d_defn)
      end
    rescue StandardError => e
      warn "[extract_graph.rb] Decode processing error on #{gem_name}: #{e.message}"
    end
  end

  def process_rubydex_definition(gem_name, gem_node_id, defn, file_path, defs_by_qualified)
    nesting = (defn.lexical_nesting || []).map(&:name).reverse
    parent_scope = nesting.join("::")

    case defn
    when Rubydex::ModuleDefinition
      mod_name = defn.name.to_s
      qname = parent_scope.empty? ? mod_name : "#{parent_scope}::#{mod_name}"
      node_id = "module:#{qname}"
      defs_by_qualified[qname] = node_id

      comments = (defn.comments || []).map { |c| c.respond_to?(:string) ? c.string : c.to_s }
      add_node(node_id, "module", mod_name, gem_name, {
        "qualified_name" => qname,
        "file" => file_path,
        "comments" => comments
      })
      parent_id = parent_scope.empty? ? gem_node_id : (defs_by_qualified[parent_scope] || "module:#{parent_scope}")
      add_edge(parent_id, node_id, "defines")

    when Rubydex::ClassDefinition
      cls_name = defn.name.to_s
      qname = parent_scope.empty? ? cls_name : "#{parent_scope}::#{cls_name}"
      node_id = "class:#{qname}"
      defs_by_qualified[qname] = node_id

      super_name = defn.super_class.to_s if defn.respond_to?(:super_class) && defn.super_class
      super_name = nil if super_name && super_name.strip.empty?

      comments = (defn.comments || []).map { |c| c.respond_to?(:string) ? c.string : c.to_s }
      add_node(node_id, "class", cls_name, gem_name, {
        "qualified_name" => qname,
        "superclass" => super_name,
        "file" => file_path,
        "comments" => comments
      })
      parent_id = parent_scope.empty? ? gem_node_id : (defs_by_qualified[parent_scope] || "module:#{parent_scope}")
      add_edge(parent_id, node_id, "defines")

      if super_name
        super_id = "class:#{super_name}"
        add_node(super_id, "class", super_name, gem_name, { "external" => true })
        add_edge(node_id, super_id, "inherits_from")
      end

    when Rubydex::MethodDefinition
      meth_name = defn.name.to_s.sub(/\(\)\z/, "")
      owner_name = parent_scope.empty? ? "Object" : parent_scope
      qname = "#{owner_name}##{meth_name}"
      node_id = "method:#{qname}"
      defs_by_qualified[qname] = node_id

      # Format parameters and signature
      params = []
      if defn.respond_to?(:signatures) && defn.signatures
        defn.signatures.each do |sig|
          if sig.instance_variable_defined?(:@parameters)
            sig.instance_variable_get(:@parameters).each do |p|
              p_name = p.instance_variable_get(:@name).to_s
              p_type = p.class.name.split("::").last.sub(/Parameter\z/, "")
              params << { "name" => p_name, "kind" => p_type }
            end
          end
        end
      end

      sig_params = params.map { |p| p["name"] }.join(", ")
      sig_text = "def #{meth_name}#{sig_params.empty? ? '' : "(#{sig_params})"}"
      comments = (defn.comments || []).map { |c| c.respond_to?(:string) ? c.string : c.to_s }

      add_node(node_id, "method", meth_name, gem_name, {
        "qualified_name" => qname,
        "owner" => owner_name,
        "signature" => sig_text,
        "parameters" => params,
        "file" => file_path,
        "comments" => comments
      })
      owner_id = defs_by_qualified[owner_name] || "class:#{owner_name}"
      add_edge(owner_id, node_id, "defines")
    end
  end

  def process_rubydex_method_reference(gem_name, mr, file_path)
    return unless mr.respond_to?(:location) && mr.location

    callee_name = mr.instance_variable_get(:@name).to_s
    return if callee_name.empty? || callee_name == "require" || callee_name == "require_relative"

    loc = mr.location
    target_id = "method:#{callee_name}"
    # Link from file / caller if identifiable
  end

  def process_decode_definition(gem_name, d_defn)
    qname = d_defn.qualified_name.to_s
    return if qname.empty?

    # Find or create corresponding target node in graph
    target_id = nil
    if qname.include?("#") || qname.include?(".")
      target_id = "method:#{qname}"
    else
      target_id = "class:#{qname}"
    end

    node = @node_ids[target_id]
    unless node
      # Create placeholder if rubydex didn't capture it
      node_type = (qname.include?("#") || qname.include?(".")) ? "method" : "class"
      node = add_node(target_id, node_type, d_defn.name.to_s, gem_name, {
        "qualified_name" => qname
      })
    end

    # Extract text and docstring
    doc = d_defn.documentation
    return unless doc

    # Collect plain docstring text
    text_lines = []
    if doc.comments
      doc.comments.each do |c|
        line = c.to_s.strip
        text_lines << line unless line.start_with?("@")
      end
    end
    docstring = text_lines.join(" ").strip
    if !docstring.empty? && node["data"]["docstring"].nil?
      node["data"]["docstring"] = docstring
    end

    # Extract children tags and examples
    example_idx = 0
    if doc.respond_to?(:children) && doc.children
      doc.children.each do |child|
        case child
        when Decode::Comment::Parameter
          p_name = child.instance_variable_get(:@name).to_s
          p_type = child.instance_variable_get(:@type).to_s
          p_desc = (child.children || []).map { |c| c.instance_variable_get(:@line).to_s }.join(" ")

          tag_id = "doc_tag:#{qname}:parameter:#{p_name}"
          add_node(tag_id, "doc_tag", "parameter:#{p_name}", gem_name, {
            "directive" => "parameter",
            "name" => p_name,
            "type" => p_type,
            "text" => p_desc,
            "target" => qname
          })
          add_edge(target_id, tag_id, "documents")

        when Decode::Comment::Returns
          r_type = child.instance_variable_get(:@type).to_s
          r_desc = (child.children || []).map { |c| c.instance_variable_get(:@line).to_s }.join(" ")

          tag_id = "doc_tag:#{qname}:returns"
          add_node(tag_id, "doc_tag", "returns", gem_name, {
            "directive" => "returns",
            "type" => r_type,
            "text" => r_desc,
            "target" => qname
          })
          add_edge(target_id, tag_id, "documents")

        when Decode::Comment::Example
          example_idx += 1
          ex_desc = (child.children || []).map { |c| c.instance_variable_get(:@line).to_s }.join("\n")
          tag_id = "example:#{qname}:#{example_idx}"
          add_node(tag_id, "example", "#{qname} example #{example_idx}", gem_name, {
            "code" => ex_desc,
            "target" => qname
          })
          add_edge(target_id, tag_id, "documents")
        end
      end
    end
  end

  def to_h
    {
      "version" => "1.0",
      "gems" => @gems.uniq,
      "nodes" => @nodes,
      "edges" => @edges
    }
  end
end

def main
  options = { output: "graph.json" }
  parser = OptionParser.new do |opts|
    opts.banner = "Usage: extract_graph.rb [options] <gem_path1> [gem_path2 ...]"
    opts.on("-o", "--output FILE", "Output JSON graph artifact path") do |o|
      options[:output] = o
    end
  end
  parser.parse!

  if ARGV.empty?
    warn "No gem paths provided."
    exit 1
  end

  extractor = GraphExtractor.new

  total_gems = ARGV.size
  ARGV.each_with_index do |gem_arg, idx|
    # Format can be gem_name=gem_path or just gem_path
    if gem_arg.include?("=")
      name, path = gem_arg.split("=", 2)
    else
      path = gem_arg
      name = File.basename(path)
    end

    unless Dir.exist?(path)
      warn "[extract_graph.rb] Directory does not exist: #{path}"
      next
    end

    puts "[extract_graph.rb] Indexing AST & docs for '#{name}' (#{idx + 1}/#{total_gems})..."
    extractor.extract_gem(name, File.expand_path(path))
  end

  graph_data = extractor.to_h
  FileUtils.mkdir_p(File.dirname(File.expand_path(options[:output])))
  File.write(options[:output], JSON.pretty_generate(graph_data))
  puts "[extract_graph.rb] Successfully wrote graph artifact to #{options[:output]} (#{graph_data['nodes'].size} nodes, #{graph_data['edges'].size} edges)"
end

main if __FILE__ == $PROGRAM_NAME
